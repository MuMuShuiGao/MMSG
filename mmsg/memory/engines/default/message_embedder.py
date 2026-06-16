"""user message embedding worker。

独立后台循环：增量扫未 embed 的 user message → 调用 embedding API → 写入 vec_message 虚表。
用水印机制避免重复，批量写向量。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from mmsg.common import parse_datetime_utc, hours_elapsed
from mmsg.memory.engines.default.vector_store import _serialize_embedding

log = logging.getLogger("mmsg.memory.message_embedder")

DEFAULT_POLL_INTERVAL = 60          # 秒
DEFAULT_BATCH_SIZE = 50
DEFAULT_MIN_HOURS = 1               # 最少 1 小时跑一次，即使没有增量也检查


class MessageEmbedder:
    """user message embedding worker。"""

    def __init__(
        self,
        store,               # SqliteStore
        embedding_provider,  # EmbeddingProvider
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        batch_size: int = DEFAULT_BATCH_SIZE,
        min_hours: int = DEFAULT_MIN_HOURS,
    ) -> None:
        self._store = store
        self._embed = embedding_provider
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._min_hours = min_hours

    @property
    def _last_embedded_id(self) -> int:
        v = self._store.get_memory_state("last_embedded_message_id")
        return int(v) if v else 0

    @_last_embedded_id.setter
    def _last_embedded_id(self, val: int) -> None:
        self._store.set_memory_state("last_embedded_message_id", str(val))

    @property
    def _last_run_at(self) -> str | None:
        return self._store.get_memory_state("embedder_last_run_at")

    @_last_run_at.setter
    def _last_run_at(self, val: str) -> None:
        self._store.set_memory_state("embedder_last_run_at", val)

    # ── 主循环 ─────────────────────────────────────

    async def serve(self) -> None:
        log.info("message embedding worker 已启动 poll=%ds batch=%d",
                 self._poll_interval, self._batch_size)

        while True:
            await asyncio.sleep(self._poll_interval)
            if not self._should_run():
                continue
            try:
                await self._embed_pending()
            except Exception:
                log.exception("message embedding 失败")

    def _should_run(self) -> bool:
        last_run = self._last_run_at
        if not last_run:
            return True
        try:
            last_dt = parse_datetime_utc(last_run)
            elapsed = hours_elapsed(last_dt)
        except (TypeError, ValueError):
            return True
        return elapsed >= self._min_hours

    # ── 核心逻辑 ───────────────────────────────────

    async def _embed_pending(self) -> None:
        last_id = self._last_embedded_id
        messages = self._store.get_user_messages_since(last_id)

        if not messages:
            log.debug("无新 user message (last_id=%d)", last_id)
            self._last_run_at = datetime.now(timezone.utc).isoformat()
            return

        total = len(messages)
        log.info("待 embed user message: %d 条 (last_id=%d)", total, last_id)

        embedded = 0
        for i in range(0, total, self._batch_size):
            batch = messages[i:i + self._batch_size]
            try:
                await self._embed_batch(batch)
                embedded += len(batch)
                batch_max_id = batch[-1]["id"]
                self._last_embedded_id = batch_max_id
                log.debug("batch %d/%d done, id up to %d", i + 1, total, batch_max_id)
            except Exception:
                log.exception("batch embed 失败 (offset=%d)，跳过此批次", i)
                # 不更新水印，下轮重试

        self._last_run_at = datetime.now(timezone.utc).isoformat()
        log.info("message embedding 完成: %d/%d", embedded, total)

    async def _embed_batch(self, messages: list[dict]) -> None:
        """批量 embed 一个 batch 的 user message。"""
        texts = [m["content"] or "" for m in messages]
        if not texts:
            return

        embeddings = await self._embed.embed(texts)
        if len(embeddings) != len(texts):
            log.error("embed batch 返回数量不匹配: %d vs %d", len(embeddings), len(texts))
            return

        for msg, vec in zip(messages, embeddings):
            self._store._conn.execute(
                "INSERT INTO vec_message (message_id, embedding) VALUES (?, ?)",
                (msg["id"], _serialize_embedding(vec)),
            )
        self._store._conn.commit()

    def get_state(self) -> dict:
        return {
            "last_embedded_id": self._last_embedded_id,
            "last_run_at": self._last_run_at,
        }
