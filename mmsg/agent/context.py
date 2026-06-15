"""LLMContext：每次 LLM 调用前的上下文组装与窗口管理。

属于推理阶段（inference-time）的 prompt 工程：
- 将 system_prompt、长期记忆、近期摘要、召回 facts、对话历史拼成 messages 列表
- 经滑动窗口 + 摘要压缩控制 token 用量后直接喂给 LLM

依赖 memory 层，与具体 agent 运行时绑定。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ..llm.base import ChatMessage
from ..memory.fact import Fact
from ..prompt.segments import SystemPromptBuilder

if TYPE_CHECKING:
    from ..memory.protocol import MemoryRuntime as Memory

log = logging.getLogger("mmsg.agent.context")


class LLMContext:
    """LLM 上下文管理器。

    组装顺序（决定 LLM 看到的上下文优先级）：
    1. system prompt    —— 来自 SystemPromptBuilder.render()
    2. 长期记忆         —— memory.md 全文，user 手动写入的持久知识
    3. 近期摘要         —— current_context.md，LLM 自动压缩的最近对话摘要
    4. history         —— Reasoner 维护的多轮对话历史（user/assistant/tool）
    5. 滑动窗口裁剪     —— 保留最近 N 轮 + 触发过期段的摘要压缩
    """

    def __init__(
        self,
        memory: Memory,
        system_builder: SystemPromptBuilder | None = None,
        max_window: int = 40,
        llm_input_turns: int = 10,
        summarize_every: int = 5,
    ) -> None:
        self._memory = memory
        self._system_builder = system_builder
        self.max_window_turns = max_window // 2
        self.llm_input_turns = llm_input_turns
        self._summarize_every = summarize_every
        self.reset_summary_state()

    def reset_summary_state(self) -> None:
        self._last_summarized_turn = 0

    async def build(
        self,
        history: list[ChatMessage],
        facts: list[Fact] | None = None,
    ) -> list[ChatMessage]:
        """组装完整 messages 列表，每次 LLM 调用前由 Reasoner 调用。"""
        msgs: list[ChatMessage] = []

        if self._system_builder is not None:
            rendered = self._system_builder.render()
            if rendered:
                msgs.append(ChatMessage(role="system", content=rendered))

        memory_ctx = self._memory.build_context_block()
        if memory_ctx:
            msgs.append(ChatMessage(role="system", content=memory_ctx))

        # 召回 facts
        if facts:
            recall_block = _format_recall_block(facts)
            if recall_block:
                msgs.append(ChatMessage(role="system", content=recall_block))

        msgs.extend(history)

        return await self._apply_sliding_window(msgs)

    async def _apply_sliding_window(
        self, msgs: list[ChatMessage]
    ) -> list[ChatMessage]:
        """滑动窗口：缓存最多 max_window_turns 轮，喂给 LLM 最多 llm_input_turns 轮。"""
        system_msgs = [m for m in msgs if m.role == "system"]
        rest = [m for m in msgs if m.role != "system"]

        if not rest:
            return system_msgs

        user_indices = [i for i, m in enumerate(rest) if m.role == "user"]
        total_turns = len(user_indices)
        start = self._last_summarized_turn
        while start + self._summarize_every <= total_turns:
            seg_users = user_indices[start : start + self._summarize_every]
            seg = rest[seg_users[0] : seg_users[-1] + 1]
            while seg and (
                seg[-1].role == "tool"
                or (seg[-1].role == "assistant" and seg[-1].tool_calls)
            ):
                seg.pop()
            if not seg:
                break
            seg_records = [
                ChatMessage(role=m.role, content=m.content or "")
                for m in seg
            ]
            self._schedule_consolidate(seg_records)
            start += self._summarize_every
        self._last_summarized_turn = start

        cache_from = self._find_cut_index(rest, self.max_window_turns)
        cached = rest[cache_from:]

        feed_from = self._find_cut_index(cached, self.llm_input_turns)
        return system_msgs + cached[feed_from:]

    def _schedule_consolidate(self, seg_records: list[ChatMessage]) -> None:
        asyncio.create_task(self._do_consolidate(seg_records))

    async def _do_consolidate(self, seg_records: list[ChatMessage]) -> None:
        try:
            await self._memory.summarize(seg_records)
        except Exception:
            log.exception("后台摘要压缩失败")

    @staticmethod
    def _find_cut_index(messages: list[ChatMessage], limit_turns: int) -> int:
        """倒序数 user 消息，数到 limit_turns 时返回起始下标。"""
        count = 0
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == "user":
                count += 1
                if count >= limit_turns:
                    return i
        return 0


def _format_recall_block(facts: list[Fact]) -> str | None:
    """将召回的 facts 格式化为 system message。"""
    if not facts:
        return None
    lines = ["# 与本次问题相关的历史记忆\n"]
    for f in facts:
        ts = f.created_at[:10]
        lines.append(f"- {f.content} [{ts}]")
    return "\n".join(lines)
