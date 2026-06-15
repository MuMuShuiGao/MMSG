"""default 记忆 — Markdown 文件 I/O 层 + 向量引擎 + 工厂。

- memory.md → 长期知识
- current_context.md → 近期上下文
- consolidate() → 委托 RecentRecapper 压缩对话为摘要
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...protocol import MarkdownMemoryLayer, MemoryEngine, MemoryRuntime
from ...fact import Fact
from .current_context import ContextWindow
from .memory import KnowledgeBase
from .vector_store import VectorStore


class DefaultMarkdownLayer(MarkdownMemoryLayer):

    def __init__(self, memory_dir: Path) -> None:
        memory_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir = memory_dir
        self.context = ContextWindow(memory_dir / "current_context.md")
        self.knowledge = KnowledgeBase(memory_dir / "memory.md")

    def get_memory_context(self) -> str | None:
        return self.knowledge.read()

    def read_recent_context(self) -> str | None:
        return self.context.read()

    def write_memory(self, content: str) -> None:
        self.knowledge.write(content)

    async def consolidate(self, messages: list) -> None:
        """短期摘要：委托 RecentRecapper 压缩对话 → current_context.md。"""
        from .recapper import RecentRecapper
        await RecentRecapper(self.context).recape(messages)


class DefaultMemoryEngine(MemoryEngine):
    """基于 sqlite-vec + FTS5 的向量引擎。"""

    def __init__(self, vector_store: VectorStore, embed_provider=None) -> None:
        self.vector_store = vector_store
        self.embed_provider = embed_provider

    async def ingest_fact(self, fact: Fact) -> int:
        return self.vector_store.insert_fact(fact, embedding=fact.embedding)

    async def query(self, query: str, k: int = 5) -> list[Fact]:
        return []  # 实际召回走 Recaller，engine.query 仅保留接口


def create(config: dict[str, Any] | None = None) -> MemoryRuntime:
    from ....config import workspace_path
    from ....llm.embedding import create_embedding_provider
    from ....storage.sqlite import SqliteStore

    config = config or {}
    memory_dir = workspace_path() / Path(config.get("memory_dir", "memory"))
    markdown_layer = DefaultMarkdownLayer(memory_dir)

    engine: MemoryEngine | None = None
    embed_provider = create_embedding_provider()
    if embed_provider:
        db_path = workspace_path() / config.get("db_path", "history.db")
        store = SqliteStore(db_path)
        vector_store = VectorStore(store._conn)
        vector_store._sqlite_store = store  # 保持 SqliteStore 引用防止被 GC
        engine = DefaultMemoryEngine(vector_store, embed_provider=embed_provider)

    return MemoryRuntime(markdown=markdown_layer, engine=engine)
