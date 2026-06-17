"""长期记忆策展 worker。

独立后台循环：增量扫 user/assistant 对话 → LLM 对比已有画像产出增量 delta → 追加到 PENDING.md。
用水位机制避免重复扫，重试上限防止卡死。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from mmsg.llm.base import ChatMessage, LLMProvider
from mmsg.common import parse_json, parse_datetime_utc, hours_elapsed, in_quiet_hours
from mmsg.memory.protocol import MarkdownMemoryLayer
from mmsg.memory.templates import MEMORY_CURATION_SYSTEM_PROMPT

log = logging.getLogger("mmsg.memory.curator")

# 触发阈值
DEFAULT_MIN_NEW_MSG = 5
DEFAULT_MIN_HOURS = 6
MAX_RETRY = 3


class MemoryCurator:
    """长期记忆策展 worker。"""

    def __init__(
        self,
        store,                    # SqliteStore
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

    @property
    def _last_curated_id(self) -> int:
        v = self._store.get_memory_state("last_curated_id")
        return int(v) if v else 0

    @_last_curated_id.setter
    def _last_curated_id(self, val: int) -> None:
        self._store.set_memory_state("last_curated_id", str(val))

    @property
    def _pending_batch_max_id(self) -> int:
        v = self._store.get_memory_state("pending_batch_max_id")
        return int(v) if v else 0

    @_pending_batch_max_id.setter
    def _pending_batch_max_id(self, val: int) -> None:
        self._store.set_memory_state("pending_batch_max_id", str(val))

    @property
    def _retry_count(self) -> int:
        v = self._store.get_memory_state("retry_count")
        return int(v) if v else 0

    @_retry_count.setter
    def _retry_count(self, val: int) -> None:
        self._store.set_memory_state("retry_count", str(val))

    @property
    def _last_run_at(self) -> str | None:
        return self._store.get_memory_state("last_run_at")

    @_last_run_at.setter
    def _last_run_at(self, val: str) -> None:
        self._store.set_memory_state("last_run_at", val)

    # ── 主循环 ─────────────────────────────────────

    async def serve(self) -> None:
        log.info("长期记忆策展 worker 已启动 min_new_msg=%d min_hours=%d",
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
                log.exception("长期记忆策展失败")

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

    # ── 策展逻辑 ───────────────────────────────────

    async def _curate(self) -> None:
        watermark = self._last_curated_id
        rows = self._store.get_messages_since(watermark, roles=["user", "assistant"])

        if not rows:
            return

        batch_max_id = max(r["id"] for r in rows)
        dialogue_lines = [f"[{r['role']}] {r['content']}" for r in rows]
        new_content = "\n".join(dialogue_lines)

        old_memory = self._markdown.get_memory_context() or ""

        system_prompt = MEMORY_CURATION_SYSTEM_PROMPT

        now = datetime.now(timezone.utc).isoformat()
        log.info("开始策展: watermark=%d batch_max=%d count=%d retry=%d",
                 watermark, batch_max_id, len(rows), self._retry_count)

        response = await self._llm.chat(
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(
                    role="user",
                    content=(
                        f"## 旧画像\n\n{old_memory}\n\n"
                        f"## 对话记录\n\n{new_content}\n\n"
                        f"当前时间：{now}"
                    ),
                ),
            ],
        )

        raw = response.message.content or ""
        data = parse_json(raw)

        if data is None:
            await self._handle_failure(batch_max_id, "JSON 解析失败")
            return

        delta = data.get("delta", "")
        if delta:
            self._markdown.write_pending(delta)

        self._last_curated_id = batch_max_id
        self._pending_batch_max_id = 0
        self._retry_count = 0
        self._last_run_at = now

        log.info(
            "策展完成: watermark=%d delta_chars=%d note=%s",
            batch_max_id, len(delta), data.get("note", ""),
        )

    async def _handle_failure(self, batch_max_id: int, reason: str) -> None:
        if self._pending_batch_max_id == batch_max_id:
            self._retry_count += 1
        else:
            self._pending_batch_max_id = batch_max_id
            self._retry_count = 1

        if self._retry_count <= self._max_retry:
            log.warning("策展失败 (%s)，重试 %d/%d", reason, self._retry_count, self._max_retry)
            return

        log.warning("重试上限已达，跳过此批: watermark=%d→%d", self._last_curated_id, batch_max_id)
        self._last_curated_id = batch_max_id
        self._pending_batch_max_id = 0
        self._retry_count = 0

    # ── 手动触发（dashboard 用）─────────────────────

    async def trigger_curate(self) -> dict:
        log.info("Dashboard | 手动触发长期记忆策展…")
        try:
            await self._curate()
            return {"ok": True}
        except Exception as e:
            log.exception("Dashboard | 策展失败")
            return {"ok": False, "error": str(e)}

    def get_state(self) -> dict:
        return {
            "last_curated_id": self._last_curated_id,
            "pending_batch_max_id": self._pending_batch_max_id,
            "retry_count": self._retry_count,
            "last_run_at": self._last_run_at,
        }
