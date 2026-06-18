"""LongMemEval haystack 灌注 — 逐 session 回放，返回 (memory, recaller, session_message_map)。

与 personamem 的 ingest_history 类似，但：
1. 接受多 session（每个 session 是独立的对话片段）
2. 记录 session_id → [message_id] 映射，供 gold fact 判定使用
3. 跳过 curator/evolver（eval 只需 consolidator 生成 facts）
"""
from __future__ import annotations

import logging
from pathlib import Path

from mmsg.llm import OpenAIProvider
from mmsg.llm.base import ChatMessage
from mmsg.memory import MemoryRuntime
from mmsg.memory.engines.default.engine import DefaultMarkdownLayer, DefaultMemoryEngine
from mmsg.storage.sqlite import SqliteStore

log = logging.getLogger("mmsg.eval.longmemeval.ingest")


async def ingest_haystack(
    sessions: list[dict],  # [{session_id, turns: [{role, content}]}]
    memory_dir: Path,
    llm: OpenAIProvider,
    embedding_provider=None,
) -> tuple[MemoryRuntime, object | None, dict[str, list[int]]]:
    """回放 haystack sessions，返回 (memory, recaller_or_None, session_message_map)。

    session_message_map: {lme_session_id: [sqlite_message_id, ...]}
    """
    markdown_layer = DefaultMarkdownLayer(memory_dir)
    store = SqliteStore(memory_dir / "ingest.db")

    vector_store = None
    engine = None
    if embedding_provider is not None:
        from mmsg.memory.engines.default.vector_store import VectorStore
        vector_store = VectorStore(store._conn)
        vector_store._sqlite_store = store
        engine = DefaultMemoryEngine(vector_store, embed_provider=embedding_provider)

    memory = MemoryRuntime(markdown=markdown_layer, engine=engine)

    consolidator = None
    if embedding_provider is not None and vector_store is not None:
        from mmsg.memory.engines.default.consolidator import Consolidator
        consolidator = Consolidator(
            store=store,
            llm=llm,
            vector_store=vector_store,
            embedding_provider=embedding_provider,
        )

    session_message_map: dict[str, list[int]] = {}
    total_turns = 0

    for session in sessions:
        sid = session["session_id"]
        turns = session.get("turns", [])
        if not turns:
            continue

        sqlite_sid = f"lme_{sid}"
        store.create_session(sqlite_sid, source="longmemeval")
        session_message_map[sid] = []

        for turn in turns:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if not content:
                continue
            msg_id = store.save_message(sqlite_sid, ChatMessage(role=role, content=content))
            session_message_map[sid].append(msg_id)
            total_turns += 1

    log.info("灌注完成: %d sessions, %d turns", len(sessions), total_turns)

    if consolidator is not None:
        log.info("Consolidator 提取 facts...")
        await consolidator._consolidate()
        log.info("Consolidator 完成，fact 数: %d", len(vector_store.all_fact_ids()))

    recaller = None
    if embedding_provider is not None and vector_store is not None:
        from mmsg.memory.recall import Recaller
        recaller = Recaller(
            memory=memory,
            llm=llm,
            embedding_provider=embedding_provider,
        )

    return memory, recaller, session_message_map
