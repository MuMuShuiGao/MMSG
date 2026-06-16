"""主动引擎主循环：醒来→整理→决策→推送。"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..agent.reason import Reasoner
from ..bus.agent import AgentBus
from ..bus.messagebus import MessageBus
from ..config import proactive as _cfg
from ..llm.base import ChatMessage, LLMProvider
from ..common import parse_json, parse_datetime_utc, hours_elapsed, in_quiet_hours
from ..memory import Memory
from ..storage.sqlite import SqliteStore
from ..tools.base import Tool
from .decision import INTENSITY_HOURS, push_score, should_push
from .notes import NoteStore
from .prompts import (
    CONSOLIDATE_PROMPT,
    CURIOSITY_PROMPT,
    PUSH_GENERATION_PROMPT,
    RESEARCH_SYSTEM_PROMPT,
)

log = logging.getLogger("mmsg.proactive")

# 整理间隔（秒），可通过 config.toml [proactive].consolidate_interval 覆盖
_DEFAULT_CONSOLIDATE_INTERVAL = 15 * 60  # 15 分钟


class ProactiveEngine:
    def __init__(
        self,
        store: SqliteStore,
        llm: LLMProvider,
        memory: Memory,
        tools: dict[str, Tool] | None = None,
        message_bus: MessageBus | None = None,
        agent_bus: AgentBus | None = None,
    ) -> None:
        self._notes = NoteStore(store._conn)
        self._store = store
        self._llm = llm
        self._memory = memory
        self._tools = tools or {}
        self._message_bus = message_bus
        self._agent_bus = agent_bus

        self._channel = _cfg("channel", "")
        self._intensity = _cfg("intensity", "medium")
        self._quiet_start = _cfg("quiet_start", "00:00")
        self._quiet_end = _cfg("quiet_end", "07:00")
        self._consolidate_interval = int(_cfg("consolidate_interval", _DEFAULT_CONSOLIDATE_INTERVAL))

        self._last_active_at: str | None = None
        self._unsub: Any = None

    # ── 主循环 ─────────────────────────────────────

    async def serve(self) -> None:
        self._last_active_at = self._notes.get_last_active_at()

        if self._agent_bus is not None:
            self._unsub = self._agent_bus.subscribe("after_turn", self._on_after_turn)

        log.info("主动引擎已启动 channel=%s intensity=%s interval=%ds",
                 self._channel, self._intensity, self._consolidate_interval)

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
                candidates = await self._consolidate()
            except Exception:
                log.exception("整理阶段失败，跳过本轮")
                continue

            if not candidates:
                continue

            best = candidates[0]  # LLM 已按 quality 排序，取最优

            if best.get("status") == "dismissed":
                continue

            quality = best.get("quality", 3)
            hours_since = self._hours_since_active()
            pushed_today = self._notes.get_pushed_today_count()

            if not should_push(quality, hours_since, pushed_today):
                log.debug(
                    "决策不推送: quality=%s silence=%.1fh pushed_today=%s",
                    quality, hours_since, pushed_today,
                )
                continue

            try:
                msg = await self._do_push(best)
            except Exception:
                log.exception("推送失败，note 保持 pending 下轮重试")
                continue

            if msg:
                log.info("已推送主动消息: %s", msg[:60])

    # ── 手动触发（供 dashboard 调试用）─────────────

    async def trigger_curiosity(self, session_id: str) -> int:
        """手动触发：从指定 session 的最近对话生成 curiosity notes。返回生成条数。"""
        log.info("Dashboard | 从 session=%s 生成 curiosity notes…", session_id)
        count = await self._generate_notes(session_id)
        log.info("Dashboard | 生成 %d 条 curiosity notes (session=%s)", count, session_id)
        return count

    async def trigger_consolidate(self) -> dict:
        """手动触发整理，返回 LLM 筛选结果。"""
        log.info("Dashboard | 开始整理 pending notes…")
        candidates = await self._consolidate()
        log.info("Dashboard | 整理完成，pending 候选 %d 条", len(candidates))
        return {"candidates": candidates, "count": len(candidates)}

    async def execute_push(self) -> dict:
        """执行真实推送：整理→决策→推送。返回结果信息。"""
        log.info("Dashboard | 执行推送 — 开始整理…")
        candidates = await self._consolidate()

        hours_since = self._hours_since_active()
        pushed_today = self._notes.get_pushed_today_count()
        quiet = self._in_quiet_hours()

        result: dict = {
            "consolidated": len(candidates),
            "hours_since_active": round(hours_since, 1),
            "pushed_today": pushed_today,
            "quiet_hours": quiet,
        }

        if not candidates:
            result["verdict"] = "no_candidates"
            return result

        best = candidates[0]
        note_id = best.get("id")

        diag = push_score(best.get("quality", 3), hours_since, pushed_today)

        if quiet:
            result["verdict"] = "blocked_by_quiet_hours"
            result["best"] = {"id": note_id, "content": best.get("content"), "score_breakdown": diag}
            return result

        if not should_push(best.get("quality", 3), hours_since, pushed_today):
            result["verdict"] = "score_too_low"
            result["best"] = {"id": note_id, "content": best.get("content"), "score_breakdown": diag}
            return result

        try:
            msg = await self._do_push(best)
        except Exception:
            log.exception("Dashboard | 推送失败")
            result["verdict"] = "push_failed"
            return result

        if not msg:
            result["verdict"] = "empty_message"
            return result

        log.info("Dashboard | 推送成功 → channel=%s msg=%s", self._channel, msg[:60])

        result["verdict"] = "pushed"
        result["best"] = {
            "id": note_id,
            "content": best.get("content"),
            "score_breakdown": diag,
            "message_sent": msg,
        }
        return result

    async def simulate_push(self) -> dict:
        """模拟完整推送流程：整理→决策→生成消息，但不实际推送。返回诊断信息。"""
        log.info("Dashboard | 模拟推送 — 开始整理…")
        candidates = await self._consolidate()

        hours_since = self._hours_since_active()
        pushed_today = self._notes.get_pushed_today_count()
        quiet = self._in_quiet_hours()

        result: dict = {
            "consolidated": len(candidates),
            "hours_since_active": round(hours_since, 1),
            "pushed_today": pushed_today,
            "quiet_hours": quiet,
            "candidates": [],
        }

        if not candidates:
            result["verdict"] = "no_candidates"
            return result

        # 逐条算分
        for item in candidates:
            diag = push_score(item.get("quality", 3), hours_since, pushed_today)
            item["_score"] = diag["score"]
            item["_would_push"] = diag["would_push"]
            result["candidates"].append(item)

        # 最优候选
        best = candidates[0]
        best_diag = push_score(best.get("quality", 3), hours_since, pushed_today)
        result["best"] = {
            "id": best.get("id"),
            "content": best.get("content"),
            "quality": best.get("quality"),
            "needs_research": best.get("needs_research"),
            "score_breakdown": best_diag,
            "would_push": best_diag["would_push"],
        }

        blocked_by_quiet = quiet and best_diag["would_push"]

        # 生成消息预览
        if best_diag["would_push"] and not quiet:
            try:
                if best.get("needs_research"):
                    msg = await self._research_and_generate(best["content"])
                else:
                    msg = await self._generate_light(best["content"])
                result["best"]["message_preview"] = msg
            except Exception:
                result["best"]["message_preview"] = "(生成失败)"
        elif blocked_by_quiet:
            result["best"]["blocked_by"] = "quiet_hours"

        result["verdict"] = "would_push" if best_diag["would_push"] and not quiet else "skip"
        log.info(
            "Dashboard | 模拟推送完成 verdict=%s candidates=%d best_quality=%s would_push=%s",
            result["verdict"], len(candidates), best.get("quality"), best_diag.get("would_push"),
        )
        return result

    # ── AfterTurn 回调 ─────────────────────────────

    async def _on_after_turn(self, evt) -> None:
        """对话结束后生成 curiosity notes。"""
        try:
            session_id = evt.payload.get("session_id", "")
            count = await self._generate_notes(session_id)
            if count:
                log.info("生成 %d 条 curiosity notes (session=%s)", count, session_id[:12])
            self._last_active_at = datetime.now(timezone.utc).isoformat()
        except Exception:
            log.exception("生成 curiosity notes 失败，跳过")

    async def _generate_notes(self, session_id: str) -> int:
        """读取最近对话，调用 LLM 生成 curiosity notes。返回生成条数。"""
        recent_msgs = self._read_recent_messages(session_id, limit=10)
        if not recent_msgs:
            return 0

        lines: list[str] = []
        for m in recent_msgs:
            role_label = {"user": "用户", "assistant": "助手", "tool": "工具"}.get(
                m.get("role", ""), m.get("role", "")
            )
            content = m.get("content", "")
            if content:
                lines.append(f"{role_label}: {content}")
        conversation = "\n".join(lines)

        ctx_block = self._memory.build_context_block()
        system_msg = ChatMessage(
            role="system",
            content=CURIOSITY_PROMPT if not ctx_block else f"{CURIOSITY_PROMPT}\n\n# 用户背景\n\n{ctx_block}",
        )
        response = await self._llm.chat(
            messages=[
                system_msg,
                ChatMessage(role="user", content=conversation),
            ],
        )
        raw = response.message.content or ""
        notes_data = self._parse_json(raw)

        if not notes_data:
            return 0

        from ..storage.models import CuriosityNote

        notes: list[CuriosityNote] = []
        for item in notes_data[:3]:
            notes.append(
                CuriosityNote(
                    session_id=session_id,
                    content=item.get("content", ""),
                    category=item.get("category", "curiosity"),
                    quality=max(1, min(5, item.get("quality", 3))),
                )
            )

        if notes:
            self._notes.save_notes(notes)

        return len(notes)

    # ── 整理 ─────────────────────────────────────

    async def _consolidate(self) -> list[dict[str, Any]]:
        """翻 pending notes，LLM 整理 + 筛选 + 打分。"""
        notes = self._notes.get_pending_notes()
        log.info("整理检查: pending notes=%d", len(notes))
        if not notes:
            return []

        # 构建 notes JSON
        notes_json = json.dumps(
            [
                {
                    "id": n.id,
                    "content": n.content,
                    "category": n.category,
                    "quality": n.quality,
                    "triggered_at": n.triggered_at,
                    "created_at": n.created_at,
                }
                for n in notes
            ],
            ensure_ascii=False,
            indent=2,
        )

        # 注入 memory 上下文
        memory_ctx = self._memory.build_context_block()

        system_parts = [CONSOLIDATE_PROMPT]
        if memory_ctx:
            system_parts.append(memory_ctx)

        response = await self._llm.chat(
            messages=[
                ChatMessage(role="system", content="\n".join(system_parts)),
                ChatMessage(role="user", content=f"请整理以下 notes：\n\n{notes_json}"),
            ],
        )
        raw = response.message.content or ""
        result = self._parse_json(raw)
        log.info("整理完成: pending_notes=%d 候选 %d 条", len(notes), len(result))
        for item in result:
            item_id = item.get("id")
            updates: dict[str, Any] = {}
            if "quality" in item:
                updates["quality"] = max(1, min(5, item["quality"]))
            if "status" in item:
                updates["status"] = item["status"]
            if "category" in item:
                updates["category"] = item["category"]
            if "content" in item:
                updates["content"] = item["content"]
            if "needs_research" in item:
                updates["needs_research"] = bool(item["needs_research"])
            if "merged_from" in item:
                updates["merged_from"] = json.dumps(item.get("merged_from") or [], ensure_ascii=False)
            if item_id is not None and updates:
                self._notes.update_note(item_id, **updates)

        # 返回 status=pending 的（候选推送）
        return [item for item in result if item.get("status") == "pending"]

    # ── 消息生成 ──────────────────────────────────

    async def _do_push(self, best: dict) -> str | None:
        """生成消息→推送→标记。返回消息文本，None 表示跳过。"""
        note_id = best.get("id")
        needs_research = best.get("needs_research", False)

        if needs_research:
            msg = await self._research_and_generate(best["content"])
        else:
            msg = await self._generate_light(best["content"])

        if not msg:
            return None

        await self._push(msg)
        if note_id is not None:
            self._notes.mark_pushed(note_id)
        return msg

    async def _generate_light(self, note_content: str) -> str:
        """轻量：直接基于 note 生成推送消息。"""
        ctx_block = self._memory.build_context_block()
        prompt = PUSH_GENERATION_PROMPT.replace("{note_content}", note_content)
        if ctx_block:
            prompt = f"{prompt}\n\n# 用户背景\n\n{ctx_block}"
        response = await self._llm.chat(
            messages=[
                ChatMessage(role="system", content=prompt),
                ChatMessage(role="user", content="生成一条消息。"),
            ],
        )
        return (response.message.content or "").strip()

    async def _research_and_generate(self, note_content: str) -> str:
        """完整 ReAct：用工具查资料后生成消息。"""
        reasoner = Reasoner(
            llm=self._llm,
            bus=self._agent_bus or AgentBus(),
            memory=self._memory,
            tools=self._tools,
            system_builder=None,
            max_steps=8,
            name="proactive",
            summarize_every=999,
        )
        # 手动注入系统提示词
        reasoner._history.append(ChatMessage(role="system", content=RESEARCH_SYSTEM_PROMPT))
        reasoner._history.append(
            ChatMessage(role="user", content=f"我想了解一下：{note_content}\n\n帮我查查相关的信息。")
        )

        final = ""
        async for chunk in reasoner.think():
            if chunk.done:
                final = chunk.content
        return final.strip()

    # ── 推送 ─────────────────────────────────────

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

        user_id = user_source[len(self._channel) + 1:]  # "qqbot:OPENID" → "OPENID"

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

    def _hours_since_active(self) -> float:
        if not self._last_active_at:
            return float(INTENSITY_HOURS.get(self._intensity, 4)) + 1  # 冷启动当作超阈值
        try:
            last = parse_datetime_utc(self._last_active_at)
            return hours_elapsed(last)
        except (TypeError, ValueError):
            return float(INTENSITY_HOURS.get(self._intensity, 4)) + 1

    def _in_quiet_hours(self) -> bool:
        return in_quiet_hours(self._quiet_start, self._quiet_end)

    def _quiet_seconds_until_end(self) -> int:
        now = datetime.now()
        h, m = self._quiet_end.split(":")
        end = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        if end <= now:
            end = end + timedelta(days=1)
        return max(1, int((end - now).total_seconds()))

    @staticmethod
    def _parse_json(raw: str) -> list[dict[str, Any]]:
        data = parse_json(raw)
        return data if isinstance(data, list) else []

    def _read_recent_messages(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        try:
            return self._store.get_messages(session_id, limit=limit)
        except Exception:
            return []
