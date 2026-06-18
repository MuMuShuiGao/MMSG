"""主动引擎主循环：醒来 → 画像链路 tick。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..bus.agent import AgentBus, AgentEvent
from ..bus.messagebus import MessageBus
from ..config import proactive as _cfg
from ..llm.base import ChatMessage, LLMProvider
from ..common import parse_datetime_utc, hours_elapsed, in_quiet_hours
from ..memory import Memory
from ..storage.sqlite import SqliteStore
from .ask_store import AskStore
from .portrait import PortraitCollector

log = logging.getLogger("mmsg.proactive")

_DEFAULT_CONSOLIDATE_INTERVAL = 15 * 60  # 15 分钟


class ProactiveEngine:
    def __init__(
        self,
        store: SqliteStore,
        llm: LLMProvider,
        memory: Memory,
        message_bus: MessageBus | None = None,
        agent_bus: AgentBus | None = None,
    ) -> None:
        self._ask = AskStore(store._conn)
        self._store = store
        self._llm = llm
        self._memory = memory
        self._message_bus = message_bus
        self._agent_bus = agent_bus

        self._channel = _cfg("channel", "")
        self._quiet_start = _cfg("quiet_start", "00:00")
        self._quiet_end = _cfg("quiet_end", "07:00")
        self._consolidate_interval = int(
            _cfg("consolidate_interval", _DEFAULT_CONSOLIDATE_INTERVAL)
        )

        self._last_active_at: str | None = None
        self._unsub: Any = None

        self._portrait = PortraitCollector(
            llm=self._llm,
            memory=self._memory,
            ask_store=self._ask,
            push_fn=self._push,
            hours_since_active_fn=self._hours_since_active,
            in_quiet_hours_fn=self._in_quiet_hours,
            recent_count_fn=self._user_msg_count_last_days,
        )

    async def serve(self) -> None:
        if self._agent_bus:
            self._unsub = self._agent_bus.subscribe(AgentEvent.AfterTurn, self._on_after_turn)

        log.info(
            "主动引擎已启动 channel=%s interval=%ds",
            self._channel, self._consolidate_interval,
        )

        while True:
            if self._in_quiet_hours():
                sleep_secs = self._quiet_seconds_until_end()
                log.debug("安静时段，休眠 %ds", sleep_secs)
                await asyncio.sleep(sleep_secs)
                continue

            await asyncio.sleep(self._consolidate_interval)

            if self._in_quiet_hours():
                continue

            try:
                await self._portrait.maybe_tick()
            except Exception:
                log.exception("画像链路本轮失败，跳过")

    # ── Dashboard 调试 ─────────────────────────────────

    def portrait_status(self) -> dict:
        """当前画像触发状态 + 下次推送预测，供 Dashboard 展示。"""
        W = PortraitCollector._WEIGHT_SILENCE
        H = PortraitCollector._ENERGY_FULL_HOURS
        threshold = PortraitCollector._SCORE_THRESHOLD
        cap = PortraitCollector._DAILY_CAP

        hours = self._hours_since_active()
        recent_msgs = self._user_msg_count_last_days(PortraitCollector._RECENT_WINDOW_DAYS)
        silence = min(1.0, hours / H)
        warmth = min(1.0, recent_msgs / PortraitCollector._RECENT_FULL_COUNT)
        score = W * silence + (1 - W) * warmth
        asked_today = self._ask.get_asked_today_count()
        quiet = self._in_quiet_hours()

        score_ready = score >= threshold
        cap_reached = asked_today >= cap

        if score_ready:
            hours_until_ready = 0.0
        else:
            h_needed = min(H, (threshold - (1 - W) * warmth) * H / W)
            hours_until_ready = round(max(0.0, h_needed - hours), 1)

        return {
            "hours_since_active": round(hours, 1),
            "recent_msgs": recent_msgs,
            "score_silence": round(silence, 3),
            "score_warmth": round(warmth, 3),
            "score": round(score, 3),
            "score_threshold": threshold,
            "score_ready": score_ready,
            "asked_today": asked_today,
            "daily_cap": cap,
            "cap_reached": cap_reached,
            "hours_until_ready": hours_until_ready if not score_ready else None,
            "quiet_now": quiet,
        }

    async def simulate_portrait(self) -> dict:
        """演练画像链路：跑完整流程但不真发。"""
        log.info("Dashboard | 模拟画像推送")
        try:
            result = await self._portrait.simulate()
            log.info("Dashboard | 模拟画像推送完成 verdict=%s", result.get("verdict"))
            return result
        except Exception:
            log.exception("Dashboard | 模拟画像推送失败")
            return {"verdict": "error"}

    async def execute_portrait(self) -> dict:
        """强制触发画像链路：跳过 K 轮 + 静默门槛，真发。"""
        log.info("Dashboard | 执行画像推送")
        try:
            result = await self._portrait.execute()
            log.info("Dashboard | 执行画像推送完成 verdict=%s", result.get("verdict"))
            return result
        except Exception:
            log.exception("Dashboard | 执行画像推送失败")
            return {"verdict": "error"}

    # ── AfterTurn 回调 ─────────────────────────────────

    async def _on_after_turn(self, evt) -> None:
        self._last_active_at = datetime.now(timezone.utc).isoformat()

    # ── 推送 ─────────────────────────────────────────

    async def _push(self, msg: str) -> None:
        if not self._message_bus or not self._channel:
            return

        target = self._resolve_push_target()
        if not target:
            log.warning("无法找到 %s 渠道的活跃 session，放弃推送", self._channel)
            return

        user_source, session_id = target

        # 写入 message 表，LLM 恢复历史时能看到主动推送的上下文
        self._store.save_message(
            session_id,
            ChatMessage(role="assistant", content=msg, meta={"proactive": True}),
        )

        user_id = user_source[len(self._channel) + 1:]

        if self._channel == "qqbot":
            payload = {"text": msg, "done": True, "openid": user_id, "proactive": True}
        elif self._channel == "feishu":
            payload = {"text": msg, "done": True, "open_id": user_id, "proactive": True}
        else:
            payload = {"text": msg, "done": True, "proactive": True}

        await self._message_bus.publish_outbound(user_source, payload)

    def _resolve_push_target(self) -> tuple[str, str] | None:
        """查找最近活跃的渠道 session，返回 (source, session_id) 或 None。"""
        try:
            sessions = self._store.list_sessions(limit=50)
            prefix = f"{self._channel}:"
            for s in sessions:
                src = s.get("source", "")
                if src.startswith(prefix):
                    return src, s["id"]
            return None
        except Exception:
            return None

    # ── 辅助 ─────────────────────────────────────

    def _user_msg_count_last_days(self, days: int) -> int:
        """近 N 天 user 消息数，用于计算 R 分量。"""
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
            row = self._store._conn.execute(
                "SELECT COUNT(*) FROM message WHERE role = 'user' AND created_at >= ?",
                (cutoff,),
            ).fetchone()
            return row[0] if row else 0
        except Exception:
            return 0

    def _hours_since_active(self) -> float:        # 优先用回调更新的时间戳；冷启动从 message 表读
        if not self._last_active_at:
            try:
                row = self._store._conn.execute(
                    "SELECT MAX(created_at) FROM message"
                ).fetchone()
                if row and row[0]:
                    self._last_active_at = row[0]
            except Exception:
                pass
        if not self._last_active_at:
            return 9999.0
        try:
            last = parse_datetime_utc(self._last_active_at)
            return hours_elapsed(last)
        except (TypeError, ValueError):
            return 9999.0

    def _in_quiet_hours(self) -> bool:
        return in_quiet_hours(self._quiet_start, self._quiet_end)

    def _quiet_seconds_until_end(self) -> int:
        now = datetime.now()
        h, m = self._quiet_end.split(":")
        end = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        if end <= now:
            end = end + timedelta(days=1)
        return max(1, int((end - now).total_seconds()))
