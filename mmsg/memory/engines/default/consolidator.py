"""事实提取 consolidator worker。

独立后台循环：增量扫 role=user 原话 → LLM 提取 facts 数组 → embed → 入向量库。
跟 curator 平行的独立 worker，各自维护水位。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from mmsg.llm.base import ChatMessage, LLMProvider
from mmsg.memory.fact import Fact
from mmsg.storage.sqlite import SqliteStore
from ._utils import parse_json

log = logging.getLogger("mmsg.memory.consolidator")

DEFAULT_MIN_NEW_MSG = 10
DEFAULT_MIN_HOURS = 2
MAX_RETRY = 3

_CONSOLIDATION_SYSTEM_PROMPT = """你是用户的记忆提取助手。

你的任务是从用户的新原话中提取原子事实，每条独立、可单独检索。

## 规则
- 一条 fact 一个事实，不要混合
- 必须保留用户原话中的专有名词、版本号、人名、项目名、技术栈名称（原文形式）
- fact 是陈述句，用第三人称描述用户的状态/偏好/事实（如"用户在 MMSG-agent 项目用 PostgreSQL 14.2"）
- 只提取用户原话里的稳定事实和偏好，不记一次性事件
- 只从用户原话提取，不要从 AI 回复里推断
- 如果用户原话里有矛盾信息，以最新为准
- 没有值得记的就返回空数组

## 输出
返回一个 JSON，只输出 JSON，不要其他文字：
{ "facts": ["用户在...", "用户偏好..."] }"""


class Consolidator:
    """事实提取 worker。"""

    def __init__(
        self,
        store: SqliteStore,
        llm: LLMProvider,
        vector_store,        # VectorStore
        embedding_provider,  # EmbeddingProvider
        min_new_msg: int = DEFAULT_MIN_NEW_MSG,
        min_hours: int = DEFAULT_MIN_HOURS,
        max_retry: int = MAX_RETRY,
        poll_interval: int = 120,
    ) -> None:
        self._store = store
        self._llm = llm
        self._vs = vector_store
        self._embed = embedding_provider
        self._min_new_msg = min_new_msg
        self._min_hours = min_hours
        self._max_retry = max_retry
        self._poll_interval = poll_interval
        self._quiet_start = "00:00"
        self._quiet_end = "07:00"

    # ── 水位 ──────────────────────────────────────

    @property
    def _last_consolidated_id(self) -> int:
        v = self._store.get_memory_state("consolidator_last_id")
        return int(v) if v else 0

    @_last_consolidated_id.setter
    def _last_consolidated_id(self, val: int) -> None:
        self._store.set_memory_state("consolidator_last_id", str(val))

    @property
    def _pending_batch_max_id(self) -> int:
        v = self._store.get_memory_state("consolidator_pending_batch_max_id")
        return int(v) if v else 0

    @_pending_batch_max_id.setter
    def _pending_batch_max_id(self, val: int) -> None:
        self._store.set_memory_state("consolidator_pending_batch_max_id", str(val))

    @property
    def _retry_count(self) -> int:
        v = self._store.get_memory_state("consolidator_retry_count")
        return int(v) if v else 0

    @_retry_count.setter
    def _retry_count(self, val: int) -> None:
        self._store.set_memory_state("consolidator_retry_count", str(val))

    @property
    def _last_run_at(self) -> str | None:
        return self._store.get_memory_state("consolidator_last_run_at")

    @_last_run_at.setter
    def _last_run_at(self, val: str) -> None:
        self._store.set_memory_state("consolidator_last_run_at", val)

    # ── 主循环 ─────────────────────────────────────

    async def serve(self) -> None:
        log.info("事实提取 consolidator 已启动 min_new_msg=%d min_hours=%d",
                 self._min_new_msg, self._min_hours)

        while True:
            await asyncio.sleep(self._poll_interval)
            if self._in_quiet_hours():
                continue
            if not self._should_run():
                continue
            try:
                await self._consolidate()
            except Exception:
                log.exception("事实提取失败")

    def _in_quiet_hours(self) -> bool:
        if self._quiet_start == self._quiet_end:
            return False
        now = datetime.now().strftime("%H:%M")
        if self._quiet_start <= self._quiet_end:
            return self._quiet_start <= now < self._quiet_end
        else:
            return now >= self._quiet_start or now < self._quiet_end

    def _should_run(self) -> bool:
        new_msgs = self._count_new_user_messages()
        if new_msgs == 0:
            return False
        if new_msgs >= self._min_new_msg:
            return True
        last_run = self._last_run_at
        if last_run:
            try:
                last_dt = datetime.fromisoformat(last_run)
                hours_since = (datetime.now(timezone.utc) - last_dt.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            except (TypeError, ValueError):
                hours_since = 999
            return hours_since >= self._min_hours
        return True

    def _count_new_user_messages(self) -> int:
        return self._store.count_user_messages_since(self._last_consolidated_id)

    # ── 提取逻辑 ───────────────────────────────────

    async def _consolidate(self) -> None:
        watermark = self._last_consolidated_id

        rows = self._store.get_user_messages_since(watermark)

        if not rows:
            return

        batch_max_id = max(r["id"] for r in rows)
        message_ids = [r["id"] for r in rows]
        user_lines = [f"- {r['content']}" for r in rows]
        new_content = "\n".join(user_lines)

        now = datetime.now(timezone.utc).isoformat()
        log.info("开始事实提取: watermark=%d batch_max=%d count=%d retry=%d",
                 watermark, batch_max_id, len(rows), self._retry_count)

        try:
            resp = await self._llm.chat(
                messages=[
                    ChatMessage(role="system", content=_CONSOLIDATION_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=f"## 用户新原话\n\n{new_content}"),
                ],
            )
            raw = resp.message.content or ""
            data = parse_json(raw)
        except Exception:
            log.exception("LLM 调用失败")
            await self._handle_failure(batch_max_id)
            return

        if data is None or not isinstance(data, dict) or "facts" not in data:
            await self._handle_failure(batch_max_id)
            return

        facts_raw = data.get("facts", [])
        if not facts_raw:
            self._last_consolidated_id = batch_max_id
            self._pending_batch_max_id = 0
            self._retry_count = 0
            self._last_run_at = now
            log.info("无新事实: watermark=%d", batch_max_id)
            return

        # embed 所有 facts
        try:
            embeddings = await self._embed.embed(facts_raw)
        except Exception:
            log.exception("Embedding 调用失败")
            await self._handle_failure(batch_max_id)
            return

        # 批量写入
        to_insert: list[tuple[Fact, list[float]]] = []
        for i, content in enumerate(facts_raw):
            if not content or not content.strip():
                continue
            fact = Fact(
                content=content.strip(),
                source_message_ids=list(message_ids),
                created_at=now,
                mention_count=1,
                last_mentioned_at=now,
                embedding=embeddings[i] if i < len(embeddings) else None,
            )
            to_insert.append((fact, fact.embedding))

        if to_insert:
            fact_ids = self._vs.insert_facts_batch(to_insert)
            log.info("事实提取完成: watermark=%d facts=%d ids=%s", batch_max_id, len(fact_ids), fact_ids[:5])

        self._last_consolidated_id = batch_max_id
        self._pending_batch_max_id = 0
        self._retry_count = 0
        self._last_run_at = now

    async def _handle_failure(self, batch_max_id: int) -> None:
        if self._pending_batch_max_id == batch_max_id:
            self._retry_count += 1
        else:
            self._pending_batch_max_id = batch_max_id
            self._retry_count = 1

        if self._retry_count <= self._max_retry:
            log.warning("事实提取失败，重试 %d/%d", self._retry_count, self._max_retry)
            return

        log.warning("重试上限已达，跳过此批: watermark=%d→%d", self._last_consolidated_id, batch_max_id)
        self._last_consolidated_id = batch_max_id
        self._pending_batch_max_id = 0
        self._retry_count = 0

    def get_state(self) -> dict:
        return {
            "last_consolidated_id": self._last_consolidated_id,
            "pending_batch_max_id": self._pending_batch_max_id,
            "retry_count": self._retry_count,
            "last_run_at": self._last_run_at,
        }
