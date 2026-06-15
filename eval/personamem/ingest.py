"""灌历史 — 逐步回放 PersonaMem 对话，模拟生产环境的 memory 演化节奏。

每 curator_every 轮 user 原话触发一次 curator 提炼画像 → memory.md
每 consolidate_every 轮触发一次 short-term consolidate → current_context.md
"""
from __future__ import annotations

import logging
from pathlib import Path

from mmsg.llm import OpenAIProvider
from mmsg.llm.base import ChatMessage
from mmsg.memory import MemoryRuntime
from mmsg.memory.engines.default.curator import MemoryCurator
from mmsg.memory.engines.default.engine import DefaultMarkdownLayer
from mmsg.storage.models import Message
from mmsg.storage.sqlite import SqliteStore

log = logging.getLogger("mmsg.eval.ingest")


async def ingest_history(
    turns: list[dict],
    memory_dir: Path,
    llm: OpenAIProvider,
    curator_every: int = 5,
    consolidate_every: int = 50,
) -> MemoryRuntime:
    """逐步回放对话，模拟生产环境的 memory 演化节奏。

    turns: [{role, content}, ...] 对话轮次列表
    memory_dir: 该样本的临时 workspace 子目录
    curator_every: 每 N 轮 user 原话触发 curator 更新 memory.md（默认 5）
    consolidate_every: 每 N 轮触发 short-term consolidate（默认 50）

    返回 memory runtime 实例（后续答题复用）
    """
    markdown_layer = DefaultMarkdownLayer(memory_dir)
    memory = MemoryRuntime(markdown=markdown_layer)

    store = SqliteStore(memory_dir / "ingest.db")
    curator = MemoryCurator(store=store, llm=llm, markdown=markdown_layer)

    user_turns = [t for t in turns if t.get("role") == "user" and t.get("content")]
    total_users = len(user_turns)

    session_id = "eval_ingest"
    store.create_session(session_id, source="eval_ingest")

    log.info("开始逐步回放 %d 轮 user 原话 (总 %d 轮)", total_users, len(turns))

    pending_consolidate: list[ChatMessage] = []
    pending_curator: int = 0
    every_user_count = 0

    for turn in turns:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if not content:
            continue

        # 写入 SQLite（curator 通过 watermark 只取增量）
        store.save_message(Message(session_id=session_id, role=role, content=content))
        pending_consolidate.append(ChatMessage(role=role, content=content))

        if role == "user":
            pending_curator += 1
            every_user_count += 1

            # 每 curator_every 轮 user 触发 curator
            if pending_curator >= curator_every:
                log.info("触发 curator (第 %d/%d 轮 user 原话)", every_user_count, total_users)
                await curator._curate()
                pending_curator = 0

        # 每 consolidate_every 轮触发 short-term consolidate
        if len(pending_consolidate) >= consolidate_every:
            log.info("触发 consolidate (%d 轮)", len(pending_consolidate))
            await memory.summarize(pending_consolidate)
            pending_consolidate.clear()

    # 收尾
    if pending_curator > 0:
        log.info("收尾 curator (%d 轮 user 原话)", pending_curator)
        await curator._curate()

    if pending_consolidate:
        log.info("收尾 consolidate (%d 轮)", len(pending_consolidate))
        await memory.summarize(pending_consolidate)

    log.info("回放完成: %d 轮 user / %d 次 curator / memory 文件已产出",             total_users, max(total_users // curator_every + 1, 1))

    return memory
