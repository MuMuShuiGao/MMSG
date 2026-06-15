"""灌历史 — 把 PersonaMem 对话原话注入 MMSG memory。

default 引擎双文件模式：
- 对话全体写入 memory.md（长期知识区，LLMContext 会读取并注入 prompt）
- 每 N 轮调用 consolidate() → LLM 摘要写入 current_context.md 头部
"""
from __future__ import annotations

from pathlib import Path

from mmsg.memory import MemoryRecord, create_memory


async def ingest_history(
    turns: list[dict],
    memory_dir: Path,
    consolidate_every: int = 50,
) -> object:
    """把对话历史灌入独立 memory 实例。

    turns: [{role, content}, ...] 对话轮次列表
    memory_dir: 该样本的临时 workspace 子目录
    consolidate_every: 每 N 轮触发一次 consolidate 摘要压缩

    返回 memory runtime 实例（后续答题复用）
    """
    memory = create_memory(config={"memory_dir": str(memory_dir)})

    # 写出完整对话历史到 memory.md（长期记忆区）
    history_text_lines = ["# 对话历史", ""]
    for turn in turns:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if not content:
            continue
        history_text_lines.append(f"**{role}**: {content}")
    memory.markdown.write_memory("\n".join(history_text_lines))

    # 分批触发 consolidate 生成近期摘要 → current_context.md
    pending: list[MemoryRecord] = []
    for turn in turns:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if not content:
            continue

        pending.append(MemoryRecord(role=role, content=content))

        if len(pending) >= consolidate_every:
            await memory.summarize(pending)
            pending.clear()

    if pending:
        await memory.summarize(pending)

    return memory
