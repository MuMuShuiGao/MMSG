"""自我认知策展 worker。

读取双方对话 → 以 MMSG 第一人称反思 → 写入 self.md。
触发节奏与 MemoryCurator 一致：5 条新消息 或 距上次 ≥6h。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from mmsg.llm.base import ChatMessage, LLMProvider
from mmsg.common import parse_json, parse_datetime_utc, hours_elapsed, in_quiet_hours
from mmsg.memory.protocol import MarkdownMemoryLayer
from mmsg.memory.templates import SELF_CURATION_SYSTEM_PROMPT

log = logging.getLogger("mmsg.memory.self_curator")

DEFAULT_MIN_NEW_MSG = 5
DEFAULT_MIN_HOURS = 6
MAX_RETRY = 3
SELF_MAX_CHARS = 2000


class SelfCurator:
    """自我认知策展 worker。"""

    def __init__(
        self,
        store,
        llm: LLMProvider,
        markdown: MarkdownMemoryLayer,
        min_new_msg: int = DEFAULT_MIN_NEW_MSG,
        min_hours: int = DEFAULT_MIN_HOURS,
        max_retry: int = MAX_RETRY,
        poll_interval: int = 300,
    ) -> None:
        self._store = store
        self._llm = llm
        self._markdown = markdown
        self._min_new_msg = min_new_msg
        self._min_hours = min_hours
        self._max_retry = max_retry
        self._poll_interval = poll_interval
        self._quiet_start = "00:00"
        self._quiet_end = "07:00"

    # ── state 属性（前缀 self_ 避免与 MemoryCurator 冲突）─────

    @property
    def _last_curated_id(self) -> int:
        v = self._store.get_memory_state("self_last_curated_id")
        return int(v) if v else 0

    @_last_curated_id.setter
    def _last_curated_id(self, val: int) -> None:
        self._store.set_memory_state("self_last_curated_id", str(val))

    @property
    def _pending_batch_max_id(self) -> int:
        v = self._store.get_memory_state("self_pending_batch_max_id")
        return int(v) if v else 0

    @_pending_batch_max_id.setter
    def _pending_batch_max_id(self, val: int) -> None:
        self._store.set_memory_state("self_pending_batch_max_id", str(val))

    @property
    def _retry_count(self) -> int:
        v = self._store.get_memory_state("self_retry_count")
        return int(v) if v else 0

    @_retry_count.setter
    def _retry_count(self, val: int) -> None:
        self._store.set_memory_state("self_retry_count", str(val))

    @property
    def _last_run_at(self) -> str | None:
        return self._store.get_memory_state("self_last_run_at")

    @_last_run_at.setter
    def _last_run_at(self, val: str) -> None:
        self._store.set_memory_state("self_last_run_at", val)

    # ── 主循环 ─────────────────────────────────────────────────

    async def serve(self) -> None:
        log.info("自我认知策展 worker 已启动 min_new_msg=%d min_hours=%d",
                 self._min_new_msg, self._min_hours)

        while True:
            await asyncio.sleep(self._poll_interval)
            if in_quiet_hours(self._quiet_start, self._quiet_end):
                continue
            if not self._should_curate():
                continue
            try:
                await self._curate()
            except Exception:
                log.exception("自我认知策展失败")

    def _should_curate(self) -> bool:
        new_msgs = self._store.count_user_messages_since(self._last_curated_id)
        if new_msgs == 0:
            return False
        if new_msgs >= self._min_new_msg:
            return True
        last_run = self._last_run_at
        if last_run:
            try:
                last_dt = parse_datetime_utc(last_run)
                hours_since = hours_elapsed(last_dt)
            except (TypeError, ValueError):
                hours_since = 999
            return hours_since >= self._min_hours
        return True

    # ── 策展逻辑 ───────────────────────────────────────────────

    async def _curate(self) -> None:
        watermark = self._last_curated_id
        rows = self._store.get_messages_since(watermark, roles=["user", "assistant"])

        if not rows:
            return

        batch_max_id = max(r["id"] for r in rows)
        dialogue_lines = [f"[{r['role']}] {r['content']}" for r in rows]
        new_content = "\n".join(dialogue_lines)

        old_self = self._markdown.get_self_context() or ""
        system_prompt = SELF_CURATION_SYSTEM_PROMPT.replace("{max_chars}", str(SELF_MAX_CHARS))

        now = datetime.now(timezone.utc).isoformat()
        log.info("开始自我认知策展: watermark=%d batch_max=%d count=%d retry=%d",
                 watermark, batch_max_id, len(rows), self._retry_count)

        response = await self._llm.chat(
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(
                    role="user",
                    content=(
                        f"## 旧自我认知\n\n{old_self}\n\n"
                        f"## 最近对话片段\n\n{new_content}\n\n"
                        f"当前时间：{now}"
                    ),
                ),
            ],
        )

        raw = response.message.content or ""
        data = parse_json(raw)

        if data is None:
            self._handle_failure(batch_max_id, "JSON 解析失败")
            return

        new_self = data.get("self", "")
        if new_self:
            self._markdown.write_self(new_self)

        self._last_curated_id = batch_max_id
        self._pending_batch_max_id = 0
        self._retry_count = 0
        self._last_run_at = now

        log.info(
            "自我认知策展完成: watermark=%d chars=%d added=%d removed=%d note=%s",
            batch_max_id, len(new_self),
            len(data.get("added", [])),
            len(data.get("removed", [])),
            data.get("note", ""),
        )

    def _handle_failure(self, batch_max_id: int, reason: str) -> None:
        if self._pending_batch_max_id == batch_max_id:
            self._retry_count += 1
        else:
            self._pending_batch_max_id = batch_max_id
            self._retry_count = 1

        if self._retry_count <= self._max_retry:
            log.warning("自我认知策展失败 (%s)，重试 %d/%d", reason, self._retry_count, self._max_retry)
            return

        log.warning("重试上限已达，跳过此批: watermark=%d→%d", self._last_curated_id, batch_max_id)
        self._last_curated_id = batch_max_id
        self._pending_batch_max_id = 0
        self._retry_count = 0

    # ── 手动触发（dashboard 用）─────────────────────────────────

    async def trigger_curate(self) -> dict:
        log.info("Dashboard | 手动触发自我认知策展…")
        try:
            await self._curate()
            return {"ok": True}
        except Exception as e:
            log.exception("Dashboard | 自我认知策展失败")
            return {"ok": False, "error": str(e)}

    def get_state(self) -> dict:
        return {
            "last_curated_id": self._last_curated_id,
            "pending_batch_max_id": self._pending_batch_max_id,
            "retry_count": self._retry_count,
            "last_run_at": self._last_run_at,
        }
