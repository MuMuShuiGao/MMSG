"""Memory 抽象协议 — 分层设计。

MarkdownMemoryLayer: 文件 I/O 层，全量注入 prompt
MemoryEngine: 向量/SQLite 引擎层，语义召回
MemoryRuntime: 组合层，上游唯一依赖
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from .fact import Fact

if TYPE_CHECKING:
    from ..llm.base import ChatMessage


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
    async def consolidate(self, messages: list[ChatMessage]) -> None:
        """摘要压缩调度。"""
        ...


# ── 向量引擎层 ───────────────────────────────────────────────

class MemoryEngine(ABC):
    """向量库 + FTS5，语义 + 关键词检索，按需召回。"""

    vector_store: Any = None       # VectorStore，子类须设置
    embed_provider: Any = None     # EmbeddingProvider，子类须设置

    @abstractmethod
    async def ingest_fact(self, fact: Fact) -> int:
        """写入一条 fact 到向量库，返回 fact_id。"""
        ...

    @abstractmethod
    async def query(self, query: str, k: int = 5) -> list[Fact]:
        """语义检索，返回 top-k 相关 facts。"""
        ...


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

    async def ingest_fact(self, fact: Fact) -> int:
        """写入一条 fact 到向量引擎，返回 fact_id。无引擎则返回 -1。"""
        if self.engine:
            return await self.engine.ingest_fact(fact)
        return -1

    async def recall(self, query: str, k: int = 5) -> list[Fact]:
        """语义召回 — 仅走向量引擎。无引擎或空 query 返回空列表。"""
        if self.engine and query:
            return await self.engine.query(query, k)
        return []

    @property
    def vector_store(self):
        return self.engine.vector_store if self.engine else None

    @property
    def embed_provider(self):
        return self.engine.embed_provider if self.engine else None

    async def summarize(self, messages: list[ChatMessage]) -> None:
        """摘要压缩 — 委托给 markdown 层的 consolidate。"""
        await self.markdown.consolidate(messages)

    def build_context_block(self) -> str:
        """返回长期记忆 + 近期摘要的拼接字符串块。

        所有 LLM 调用的 system prompt 前置注入用。
        若无内容则返回空字符串。
        """
        parts: list[str] = []
        mem = self.markdown.get_memory_context()
        if mem:
            parts.append(f"# 长期记忆\n\n{mem}")
        recent = self.markdown.read_recent_context()
        if recent:
            parts.append(recent)
        return "\n\n".join(parts)


# ── 向后兼容别名 ─────────────────────────────────────────────
Memory = MemoryRuntime
