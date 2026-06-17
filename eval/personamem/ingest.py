"""灌历史 — 逐步回放 PersonaMem 对话，模拟生产环境的 memory 演化节奏。

每 curator_every 轮 user 原话触发一次 curator → PENDING.md
每 recapper_every 轮触发一次 recapper → current_context.md
灌完后强制跑一次 Evolver（PENDING → memory.md + self.md）
若传入 embedding_provider，还跑 Consolidator（user 原话 → fact 向量库）并构建 Recaller
"""
from __future__ import annotations

import logging
from pathlib import Path

from mmsg.llm import OpenAIProvider
from mmsg.llm.base import ChatMessage
from mmsg.memory import MemoryRuntime
from mmsg.memory.engines.default.curator import MemoryCurator
from mmsg.memory.engines.default.engine import DefaultMarkdownLayer, DefaultMemoryEngine
from mmsg.memory.engines.default.evolver import Evolver
from mmsg.storage.sqlite import SqliteStore

log = logging.getLogger("mmsg.eval.ingest")


async def ingest_history(
    turns: list[dict],
    memory_dir: Path,
    llm: OpenAIProvider,
    curator_every: int = 5,
    recapper_every: int = 50,
    embedding_provider=None,
) -> tuple[MemoryRuntime, object | None]:
    """逐步回放对话，模拟生产环境的 memory 演化节奏。

    turns: [{role, content}, ...] 对话轮次列表
    memory_dir: 该样本的临时 workspace 子目录
    curator_every: 每 N 轮 user 原话触发 curator（默认 5）
    recapper_every: 每 N 轮触发 recapper → current_context.md（默认 50）
    embedding_provider: 可选，传入则启用 Consolidator + Recaller

    返回 (memory, recaller_or_None)
    """
    markdown_layer = DefaultMarkdownLayer(memory_dir)
    store = SqliteStore(memory_dir / "ingest.db")

    # 向量引擎（可选）
    vector_store = None
    engine = None
    if embedding_provider is not None:
        from mmsg.memory.engines.default.vector_store import VectorStore
        vector_store = VectorStore(store._conn)
        vector_store._sqlite_store = store
        engine = DefaultMemoryEngine(vector_store, embed_provider=embedding_provider)

    memory = MemoryRuntime(markdown=markdown_layer, engine=engine)

    curator = MemoryCurator(store=store, llm=llm, markdown=markdown_layer)
    evolver = Evolver(store=store, llm=llm, markdown=markdown_layer)

    consolidator = None
    if embedding_provider is not None and vector_store is not None:
        from mmsg.memory.engines.default.consolidator import Consolidator
        consolidator = Consolidator(
            store=store,
            llm=llm,
            vector_store=vector_store,
            embedding_provider=embedding_provider,
        )

    session_id = "eval_ingest"
    store.create_session(session_id, source="eval_ingest")

    user_turns = [t for t in turns if t.get("role") == "user" and t.get("content")]
    total_users = len(user_turns)
    log.info("开始逐步回放 %d 轮 user 原话 (总 %d 轮)", total_users, len(turns))

    pending_recapper: list[ChatMessage] = []
    pending_curator: int = 0
    every_user_count = 0

    for turn in turns:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if not content:
            continue

        cm = ChatMessage(role=role, content=content)
        store.save_message(session_id, cm)
        pending_recapper.append(cm)

        if role == "user":
            pending_curator += 1
            every_user_count += 1

            if pending_curator >= curator_every:
                log.info("触发 curator (%d/%d)", every_user_count, total_users)
                await curator._curate()
                pending_curator = 0

        if len(pending_recapper) >= recapper_every:
            log.info("触发 recapper (%d 轮)", len(pending_recapper))
            await memory.summarize(pending_recapper)
            pending_recapper.clear()

    # 收尾 curator
    if pending_curator > 0:
        log.info("收尾 curator (%d 轮)", pending_curator)
        await curator._curate()

    # 收尾 recapper
    if pending_recapper:
        log.info("收尾 recapper (%d 轮)", len(pending_recapper))
        await memory.summarize(pending_recapper)

    # Evolver：强制消化 PENDING → memory.md + self.md
    pending = markdown_layer.read_pending()
    if pending:
        log.info("Evolver 消化 PENDING (%d chars)...", len(pending))
        await evolver._evolve()
    else:
        log.info("PENDING 为空，跳过 Evolver")

    # Consolidator：强制提取 user 原话 → fact 向量库
    if consolidator is not None:
        log.info("Consolidator 提取 fact...")
        await consolidator._consolidate()

    # Recaller
    recaller = None
    if embedding_provider is not None and vector_store is not None:
        from mmsg.memory.recall import Recaller
        recaller = Recaller(
            memory=memory,
            llm=llm,
            embedding_provider=embedding_provider,
        )
        log.info("Recaller 已构建")

    log.info(
        "回放完成: user=%d curator_runs=%d evolver=%s consolidator=%s",
        total_users,
        max(total_users // curator_every + 1, 1),
        "yes" if pending else "skip",
        "yes" if consolidator else "skip(no embed)",
    )

    return memory, recaller
