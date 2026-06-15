"""单样本 runner — 临时 workspace → 灌历史 → 答题 → 评分。"""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Any

from mmsg.llm import OpenAIProvider

from .answer import ask_question, build_agent
from .grader import grade
from .ingest import ingest_history


async def run_one_sample(
    sample: dict[str, Any],
    llm: OpenAIProvider,
    base_temp_dir: Path,
    consolidate_every: int = 50,
) -> dict:
    """对一条样本跑完 MCQ 并返回结果。

    sample: {question_id, question_type, user_question, correct_answer,
             all_options, history_turns, ...}
    llm: 共享 LLM 实例
    base_temp_dir: 临时 workspace 根目录

    返回 {question_id, predicted, gold, correct, question_type, usage, raw, elapsed_ms, ...}
    """
    question_id = sample.get("question_id", "unknown")
    persona_id = sample.get("persona_id", "unknown")
    workspace = base_temp_dir / f"{persona_id}_{question_id[:8]}"
    workspace.mkdir(parents=True, exist_ok=True)
    memory_dir = workspace / "memory"

    try:
        # 1. 灌历史
        memory = await ingest_history(
            sample.get("history_turns", []),
            memory_dir,
            consolidate_every=consolidate_every,
        )

        # 2. 构建 agent
        agent = build_agent(llm, memory, workspace)

        # 3. 组装题目：问题 + 选项
        options = sample.get("all_options", [])
        question_text = _format_question(sample.get("user_question", ""), options)

        t0 = time.perf_counter()
        ans = await ask_question(agent, question_text)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        grading = grade(ans["predicted"], sample.get("correct_answer", ""))

        return {
            "question_id": question_id,
            "persona_id": persona_id,
            "question_type": sample.get("question_type", "unknown"),
            "topic": sample.get("topic", ""),
            "question_text": question_text,
            "predicted": grading["predicted"],
            "gold": grading["gold"],
            "correct": grading["correct"],
            "reason": grading["reason"],
            "raw": ans["raw"],
            "steps": ans["steps"],
            "usage": ans["usage"],
            "elapsed_ms": int(elapsed_ms),
        }

    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _format_question(user_question: str, options: list[str]) -> str:
    """组装题目文字：用户消息 + 选项列表。"""
    if not options:
        return user_question

    lines = [user_question, ""]
    for opt in options:
        lines.append(opt)
    return "\n".join(lines)


def run_one_sample_sync(
    sample: dict[str, Any],
    llm: OpenAIProvider,
    base_temp_dir: Path,
    consolidate_every: int = 50,
) -> dict:
    """同步封装。"""
    return asyncio.run(
        run_one_sample(sample, llm, base_temp_dir, consolidate_every)
    )
