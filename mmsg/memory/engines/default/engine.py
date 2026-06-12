"""default 记忆引擎 — 双文件持久化。
实现 Memory 协议，内部组合 ContextWindow + KnowledgeBase。
摘要压缩由推理引擎的滑动窗口触发，此处只负责摘要生成与写入。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...protocol import Memory
from ...record import MemoryRecord
from .current_context import ContextWindow
from .memory import KnowledgeBase

log = logging.getLogger("mmsg.memory.default")


class DefaultEngine(Memory):
    layer = "default"

    def __init__(self, memory_dir: Path, max_turns: int = 5, summarize_every: int = 10) -> None:
        memory_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir = memory_dir
        self.context = ContextWindow(memory_dir / "current_context.md", max_turns=max_turns)
        self.knowledge = KnowledgeBase(memory_dir / "memory.md")

    async def write(self, record: MemoryRecord) -> None:
        pass

    async def recall(self, query: str, k: int = 8) -> list[MemoryRecord]:
        records: list[MemoryRecord] = []
        content = self.knowledge.read()
        if content:
            records.append(MemoryRecord(role="system", content=f"# 长期记忆\n\n{content}"))
        content = self.context.read()
        if content:
            records.append(MemoryRecord(role="system", content=content))
        return records

    async def summarize(self, messages: list[MemoryRecord]) -> None:
        """将一段对话记录压缩为摘要，写入 current_context.md 近期摘要。"""
        try:
            from ....llm.base import ChatMessage
            from ....core import llm_registry
            llm = llm_registry.create("openai")
        except Exception:
            log.exception("无法创建 LLM 实例用于摘要")
            return

        turns_text = "\n---\n".join(
            f"{m.role}: {m.content or ''}" for m in messages if m.role in ("user", "assistant")
        )
        prompt = (
            "根据以下多轮对话，返回一个 JSON 对象，包含 5 个字段（每字段不超过40字，无则填\"无\"）：\n"
            '{\n'
            '  "持续关注": "...",\n'
            '  "明确偏好": "...",\n'
            '  "待延续话题": "...",\n'
            '  "避免事项": "...",\n'
            '  "前置背景": "..."\n'
            '}\n'
            f"\n对话内容：\n{turns_text}"
        )
        msgs = [ChatMessage(role="user", content=prompt)]
        try:
            resp = await llm.chat(msgs)
            raw = resp.message.content.strip() or ""
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                if raw.endswith("```"):
                    raw = raw.rsplit("\n", 1)[0]
            data = json.loads(raw)
            lines = [
                f"- 最近持续关注：{data.get('持续关注', '无')}",
                f"- 最近明确偏好：{data.get('明确偏好', '无')}",
                f"- 最近待延续话题：{data.get('待延续话题', '无')}",
                f"- 最近避免事项：{data.get('避免事项', '无')}",
                f"- 会话前置背景：{data.get('前置背景', '无')}",
            ]
            summary = "\n".join(lines)

            ts = datetime.now(timezone.utc).strftime("%m-%d %H:%M")
            entry = f"### [{ts}]\n{summary}\n"
            existing = self.context.read() or ""
            if "# 近期摘要" in existing:
                idx = existing.index("# 近期摘要") + len("# 近期摘要")
                new_content = "# 近期摘要\n" + entry + existing[idx:].lstrip("\n")
            else:
                new_content = "# 近期摘要\n" + entry
            self.context.write(new_content)
        except Exception:
            log.exception("摘要生成失败")


def create(config: dict[str, Any] | None = None) -> Memory:
    from ....config import workspace_path

    config = config or {}
    memory_dir = workspace_path() / Path(config.get("memory_dir", "memory"))
    max_turns = config.get("max_turns", 5)
    summarize_every = config.get("summarize_every", 10)
    return DefaultEngine(memory_dir, max_turns=max_turns, summarize_every=summarize_every)
