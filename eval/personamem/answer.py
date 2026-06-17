"""答题 — 走 AgentLoop 真实链路回答 MCQ，提取字母答案。"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from mmsg.agent import AgentLoop
from mmsg.bus.agent import AgentBus
from mmsg.bus.messagebus import MessageBus
from mmsg.llm import OpenAIProvider
from mmsg.prompt.segments import SystemPromptBuilder
from mmsg.storage import SqliteStore


_MCQ_SUFFIX = "\n\n请只输出 A、B、C 或 D 一个字母作为答案。"


def build_agent(
    llm: OpenAIProvider,
    memory: object,
    workspace_dir: Path,
    recaller=None,
) -> AgentLoop:
    """构造独立 AgentLoop，隔离 storage、bus、tools。"""
    agent_bus = AgentBus()
    message_bus = MessageBus()
    store = SqliteStore(workspace_dir / "eval_history.db")
    tools: dict = {}

    return AgentLoop(
        agent_bus=agent_bus,
        llm=llm,
        memory=memory,
        tools=tools,
        message_bus=message_bus,
        system_builder=SystemPromptBuilder(workspace=workspace_dir),
        storage=store,
        recaller=recaller,
    )


async def ask_question(
    agent: AgentLoop,
    question_text: str,
) -> dict:
    """喂一道 MCQ 给 agent，返回提取的答案字母和原始输出。

    返回: {predicted, raw, steps, usage}
    """
    prompt = question_text + _MCQ_SUFFIX

    source_id = uuid.uuid4().hex[:8]
    raw_output = ""
    total_usage: dict = {}
    steps = 0

    async for chunk in agent.run(prompt, source=source_id):
        total_usage = chunk.usage
        steps = chunk.steps
        raw_output = chunk.content

    return {
        "predicted": _extract_letter(raw_output),
        "raw": raw_output,
        "steps": steps,
        "usage": total_usage,
    }


def _extract_letter(text: str) -> str | None:
    """从 agent 输出中提取 A/B/C/D 字母。

    先精确匹配「只输出一个字母」的模版，失败则宽松匹配。
    """
    if not text:
        return None
    t = text.strip()

    # 理想情况：纯字母
    if len(t) == 1 and t.upper() in "ABCD":
        return t.upper()

    # 手动提取：找最后一个独立的 A/B/C/D
    for pattern in [
        r'\b([ABCDabcd])\s*[,.;]?\s*$',
        r'答案[：:是为]\s*([ABCDabcd])',
        r'([ABCDabcd])\s*$',
    ]:
        m = re.search(pattern, t)
        if m:
            return m.group(1).upper()

    return None
