"""Memory 抽象协议 — 分层设计。

MarkdownMemoryLayer: 文件 I/O 层，全量注入 prompt
MemoryEngine: 向量/SQLite 引擎层，语义召回
MemoryRuntime: 组合层，上游唯一依赖
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .record import MemoryRecord


# ── Markdown 文件 I/O 层 ─────────────────────────────────────

class MarkdownMemoryLayer(ABC):
    """读写 .md 文件，全量塞进 prompt。不做检索。"""

    @abstractmethod
    def get_memory_context(self) -> str | None:
        """读取长期记忆 (MEMORY.md)。"""
        ...

    @abstractmethod
    def read_recent_context(self) -> str | None:
        """读取近期摘要 (RECENT_CONTEXT.md / current_context.md)。"""
        ...

    @abstractmethod
    def write_memory(self, content: str) -> None:
        """写入长期记忆。"""
        ...

    @abstractmethod
    def write_recent_context(self, content: str) -> None:
        """写入近期摘要。"""
        ...

    async def consolidate(self, messages: list[MemoryRecord]) -> None:
        """摘要压缩调度 — 可选实现。"""
        pass


# ── 向量引擎层 ───────────────────────────────────────────────

class MemoryEngine(ABC):
    """向量库 + SQLite，语义检索，按需召回。"""

    @abstractmethod
    async def ingest(self, record: MemoryRecord) -> None:
        """写入一条记录到向量库。"""
        ...

    @abstractmethod
    async def query(self, query: str, k: int = 8) -> list[MemoryRecord]:
        """语义检索，返回 top-k 相关记录。"""
        ...

    async def mutate(self, record_id: str, updates: dict) -> None:
        """修改/删除记录 — 可选实现。"""
        pass


# ── 组合层 MemoryRuntime ─────────────────────────────────────

class MemoryRuntime:
    """上游唯一依赖。组合 markdown 层 + 可选的向量引擎。

    上游通过 .markdown / .engine 分别调用两条路径。
    """

    def __init__(
        self,
        markdown: MarkdownMemoryLayer,
        engine: MemoryEngine | None = None,
    ) -> None:
        self.markdown = markdown
        self.engine = engine

    async def write(self, record: MemoryRecord) -> None:
        """写入一条对话记录到向量引擎（如有）。"""
        if self.engine:
            await self.engine.ingest(record)

    async def recall(self, query: str, k: int = 8) -> list[MemoryRecord]:
        """语义召回 — 仅走向量引擎。无引擎或空 query 返回空列表。"""
        if self.engine and query:
            return await self.engine.query(query, k)
        return []

    async def summarize(self, messages: list[MemoryRecord]) -> None:
        """摘要压缩 — 委托给 markdown 层的 consolidate。"""
        await self.markdown.consolidate(messages)


# ── 向后兼容别名 ─────────────────────────────────────────────
Memory = MemoryRuntime
