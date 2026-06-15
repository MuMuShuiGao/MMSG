"""事实合并 worker：定期合并 cos > 0.97 的近重复 facts。

保留最新原文 + 合并 source_message_ids + 累计 mention_count。
3 天跑一次。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .vector_store import VectorStore

log = logging.getLogger("mmsg.memory.merger")

DEFAULT_MIN_DAYS = 3
DEFAULT_SIMILARITY_THRESHOLD = 0.97


class Merger:
    """事实合并 worker。"""

    def __init__(
        self,
        store,           # SqliteStore
        vector_store: VectorStore,
        embedding_provider,  # EmbeddingProvider
        min_days: int = DEFAULT_MIN_DAYS,
        poll_interval: int = 3600,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> None:
        self._store = store
        self._vs = vector_store
        self._embed = embedding_provider
        self._min_days = min_days
        self._poll_interval = poll_interval
        self._threshold = similarity_threshold

    @property
    def _last_merge_at(self) -> str | None:
        return self._store.get_memory_state("merger_last_run_at")

    @_last_merge_at.setter
    def _last_merge_at(self, val: str) -> None:
        self._store.set_memory_state("merger_last_run_at", val)

    async def serve(self) -> None:
        log.info("事实合并 worker 已启动 min_days=%d threshold=%.2f",
                 self._min_days, self._threshold)

        while True:
            await asyncio.sleep(self._poll_interval)
            if not self._should_run():
                continue
            try:
                await self._merge()
            except Exception:
                log.exception("事实合并失败")

    def _should_run(self) -> bool:
        last_run = self._last_merge_at
        if not last_run:
            return True
        try:
            last_dt = datetime.fromisoformat(last_run)
            days_since = (datetime.now(timezone.utc) - last_dt.replace(tzinfo=timezone.utc)).total_seconds() / 86400
        except (TypeError, ValueError):
            return True
        return days_since >= self._min_days

    async def _merge(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        log.info("开始事实合并…")

        fact_ids = self._vs.all_fact_ids()
        if len(fact_ids) < 2:
            log.info("事实数 < 2，跳过合并")
            self._last_merge_at = now
            return

        # 按 id 降序（最新的优先），逐条找近重复
        grouped: dict[int, list[int]] = {}  # survivor → absorbed
        absorbed_global: set[int] = set()

        for fid in sorted(fact_ids, reverse=True):
            if fid in absorbed_global:
                continue
            vec = self._vs.get_fact_embedding(fid)
            if not vec:
                continue
            candidates = self._vs.find_near_duplicates(vec, self._threshold, limit=10)
            absorbed = [
                c[0].id for c in candidates
                if c[0].id and c[0].id != fid and c[0].id not in absorbed_global
            ]
            if absorbed:
                grouped[fid] = absorbed
                absorbed_global.update(absorbed)

        if not grouped:
            log.info("无近重复事实")
            self._last_merge_at = now
            return

        for survivor_id, absorbed_ids in grouped.items():
            try:
                self._vs.merge_facts(survivor_id, absorbed_ids)
                log.info("合并: %d ← %s", survivor_id, absorbed_ids)
            except Exception:
                log.exception("合并失败: %d ← %s", survivor_id, absorbed_ids)

        self._last_merge_at = now
        log.info("事实合并完成: %d 组", len(grouped))

    def get_state(self) -> dict:
        return {
            "last_merge_at": self._last_merge_at,
        }
