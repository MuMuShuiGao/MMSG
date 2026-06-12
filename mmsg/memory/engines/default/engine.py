"""default 记忆 — Markdown 文件 I/O 层实现。

实现 MarkdownMemoryLayer 协议：
- memory.md → 长期记忆
- current_context.md → 近期摘要
- consolidate() → LLM 压缩对话写入近期摘要
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...protocol import MarkdownMemoryLayer, MemoryRuntime
from ...record import MemoryRecord
from .current_context import ContextWindow
from .memory import KnowledgeBase

log = logging.getLogger("mmsg.memory.default")


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

    def write_recent_context(self, content: str) -> None:
        self.context.write(content)

    async def consolidate(self, messages: list[MemoryRecord]) -> None:
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


def create(config: dict[str, Any] | None = None) -> MemoryRuntime:
    from ....config import workspace_path

    config = config or {}
    memory_dir = workspace_path() / Path(config.get("memory_dir", "memory"))
    markdown_layer = DefaultMarkdownLayer(memory_dir)
    return MemoryRuntime(markdown=markdown_layer, engine=None)
