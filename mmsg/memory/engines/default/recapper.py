"""短期摘要压缩：LLM 将 N 轮对话压成 5 字段摘要 → 写入 current_context.md。

由 reasoner 的滑动窗口触发，fire-and-forget 异步调用。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from mmsg.llm.base import ChatMessage
from ._utils import parse_json

log = logging.getLogger("mmsg.memory.recapper")


class RecentRecapper:
    """短期摘要压缩器。

    委托 DefaultMarkdownLayer 的 consolidate() 调用到这里。
    """

    def __init__(self, context_window) -> None:
        self._context = context_window  # ContextWindow 实例

    async def recape(self, messages: list[ChatMessage]) -> None:
        """将一段对话记录压缩为摘要，写入 current_context.md。"""
        try:
            from mmsg.core import llm_registry
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
            data = parse_json(raw)
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
            self._context.write(f"# 近期摘要\n{entry}")
        except Exception:
            log.exception("摘要生成失败")
