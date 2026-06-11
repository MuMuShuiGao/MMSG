"""default 记忆引擎 — 双文件持久化 + 内建摘要策略。

实现 Memory 协议，内部组合 ContextWindow + KnowledgeBase。
end_turn 时自动累加轮次，每 10 轮触发 LLM 摘要压缩。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...protocol import Memory
from ...record import MemoryRecord
from .current_context import ContextWindow
from .memory import KnowledgeBase


class DefaultEngine(Memory):
    layer = "default"

    def __init__(self, memory_dir: Path, max_turns: int = 5, summarize_every: int = 10) -> None:
        memory_dir.mkdir(parents=True, exist_ok=True)
        self.context = ContextWindow(memory_dir / "current_context.md", max_turns=max_turns)
        self.knowledge = KnowledgeBase(memory_dir / "memory.md")
        self._summarize_every = summarize_every
        self._turn_count = 0
        self._pending: list[str] = []

    async def start_turn(self) -> None:
        await self.context.start_turn()

    async def write(self, record: MemoryRecord) -> None:
        await self.context.write(record.role, record.content)

    async def recall(self, query: str, k: int = 8) -> list[MemoryRecord]:
        records: list[MemoryRecord] = []
        content = self.knowledge.read()
        if content:
            records.append(MemoryRecord(role="system", content=f"# 长期记忆\n\n{content}"))
        content = self.context.read()
        if content:
            records.append(MemoryRecord(role="system", content=content))
        return records

    async def end_turn(self, user_input: str = "", assistant_output: str = "") -> None:
        self._turn_count += 1
        self._pending.append(f"用户：{user_input}\n助手：{assistant_output[:200]}")

        summary = ""
        if self._turn_count % self._summarize_every == 0:
            buffer = "\n---\n".join(self._pending)
            summary = await self._summarize(buffer)
            self._pending.clear()

        await self.context.end_turn(summary)

    async def _summarize(self, turns_text: str) -> str:
        import json
        import logging

        from ....llm.base import ChatMessage
        from ....core import llm_registry

        log = logging.getLogger("mmsg.memory.default")
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
            llm = llm_registry.create("openai")
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
            return "\n".join(lines)
        except Exception:
            log.exception("摘要生成失败")
            return ""


def create(config: dict[str, Any] | None = None) -> Memory:
    from ....config import workspace_path

    config = config or {}
    memory_dir = workspace_path() / Path(config.get("memory_dir", "memory"))
    max_turns = config.get("max_turns", 5)
    summarize_every = config.get("summarize_every", 10)
    return DefaultEngine(memory_dir, max_turns=max_turns, summarize_every=summarize_every)
