"""推理引擎：完整的 ReAct 循环 — LLM 调用 + 流式收集 + 工具执行 + 窗口管理。

think() = 从 memory 读上下文 → 多步推理 & 工具调用 → 写回 memory & 返回记录。
AgentLoop 只负责消息总线消费和组件编排，不问"怎么多轮调工具"。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from pydantic import BaseModel, Field

from ...bus.agent import AgentEvent, AgentBus
from ...llm.base import ChatMessage, LLMProvider, ToolCall
from ...memory import Memory, MemoryRecord
from ...storage.models import TurnRecord
from ...tools.base import Tool

log = logging.getLogger("mmsg.agent.reason")


class ReasoningResult(BaseModel):
    """单步推理结果。"""
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    step: int = 0


class ThinkingResult(BaseModel):
    """完整 ReAct 循环结果。done=False 表示中间 step，done=True 表示最终结果。"""
    content: str = ""
    records: list[TurnRecord] = Field(default_factory=list)
    steps: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)
    done: bool = False


class Reasoner:
    """推理引擎：封装完整 ReAct 循环。

    职责：
    - 上下文窗口管理（memory recall → ChatMessage 组装）
    - 多步推理循环（LLM 调用 + AfterReasoning 拦截）
    - 工具执行（BeforeToolCall / AfterToolCall 事件）
    - 中间记录写回 memory
    - 返回 ThinkingResult 供上层持久化
    """

    def __init__(
        self,
        llm: LLMProvider,
        bus: AgentBus,
        memory: Memory,
        tools: dict[str, Tool],
        system_prompt: str,
        max_steps: int = 8,
        name: str = "agent",
    ) -> None:
        self.llm = llm
        self.bus = bus
        self.memory = memory
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.name = name
        self.max_window = 40
        self.max_window_turns = self.max_window // 2
        self.llm_input_turns = 10
        self._summarize_every = 5
        self._last_summarized_turn = 0

    async def think(self) -> AsyncGenerator[ThinkingResult, None]:
        """执行完整 ReAct 循环，每个 step 完成后 yield 一个 ThinkingResult。

        done=False：中间 step；done=True：最终结果，包含完整 records。
        从 memory 读上下文，多步推理 + 工具调用，中间结果写回 memory。
        """
        records: list[TurnRecord] = []
        tool_schemas = [t.schema() for t in self.tools.values()] or None
        final_text = ""
        total_usage: dict[str, Any] = {}
        step = 0

        for step in range(1, self.max_steps + 1):
            msgs = await self._assemble_messages()

            result = await self._reason_step(msgs, tool_schemas, step)
            total_usage = result.usage or total_usage

            tc_dumps = [tc.model_dump() for tc in result.tool_calls]
            await self.memory.write(
                MemoryRecord(
                    role="assistant",
                    content=result.content or "",
                    meta={"tool_calls": tc_dumps},
                )
            )
            records.append(
                TurnRecord(
                    role="assistant",
                    content=result.content or "",
                    meta={"tool_calls": tc_dumps},
                )
            )

            if not result.tool_calls:
                final_text = result.content
                await self.bus.observe(
                    AgentEvent.AfterStep,
                    self.name,
                    {"step": step, "final": True, "text": final_text},
                )
                yield ThinkingResult(
                    content=final_text,
                    records=list(records),
                    steps=step,
                    usage=total_usage,
                    done=True,
                )
                return

            for tc in result.tool_calls:
                await self.bus.observe(
                    AgentEvent.BeforeToolCall,
                    self.name,
                    {"step": step, "id": tc.id, "name": tc.name, "arguments": tc.arguments},
                )
                tool = self.tools.get(tc.name)
                if tool is None:
                    tool_result: Any = f"错误: 工具 '{tc.name}' 未注册"
                else:
                    try:
                        tool_result = await tool.run(**tc.arguments)
                    except Exception:
                        log.exception("工具 %s 执行失败", tc.name)
                        tool_result = f"错误: 工具 '{tc.name}' 执行异常"

                await self.memory.write(
                    MemoryRecord(
                        role="tool",
                        content=str(tool_result),
                        meta={"tool_call_id": tc.id, "name": tc.name},
                    )
                )
                records.append(
                    TurnRecord(
                        role="tool",
                        content=str(tool_result),
                        meta={"tool_call_id": tc.id, "name": tc.name},
                    )
                )
                await self.bus.observe(
                    AgentEvent.AfterToolCall,
                    self.name,
                    {"id": tc.id, "name": tc.name, "result": str(tool_result)},
                )

            await self.bus.observe(
                AgentEvent.AfterStep, self.name, {"step": step, "final": False}
            )
            yield ThinkingResult(
                content=result.content or "",
                records=list(records),
                steps=step,
                usage=total_usage,
                done=False,
            )

        # max_steps 耗尽
        yield ThinkingResult(
            content=final_text, records=records, steps=step, usage=total_usage, done=True
        )

    # ── 内部 ──────────────────────────────────────

    async def _reason_step(
        self,
        messages: list[ChatMessage],
        tool_schemas: list[dict[str, Any]] | None,
        step: int,
    ) -> ReasoningResult:
        """单步推理：BeforeStep 拦截 → LLM chat_stream → AfterReasoning 拦截。"""
        await self.bus.intercept(
            AgentEvent.BeforeStep,
            self.name,
            {
                "step": step,
                "messages": [m.model_dump() for m in messages],
                "tools": [t["function"]["name"] for t in (tool_schemas or [])],
            },
        )

        chunks = self.llm.chat_stream(messages, tools=tool_schemas)

        collected_content = ""
        collected_tool_calls: list[ToolCall] = []
        finish_reason: str | None = None
        usage: dict[str, Any] = {}

        async for chunk in chunks:
            if chunk.text:
                collected_content += chunk.text
            if chunk.tool_calls:
                collected_tool_calls = chunk.tool_calls
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason
            if chunk.usage:
                usage = chunk.usage

        resp_evt = await self.bus.intercept(
            AgentEvent.AfterReasoning,
            self.name,
            {
                "step": step,
                "content": collected_content,
                "tool_calls": [tc.model_dump() for tc in collected_tool_calls],
                "finish_reason": finish_reason,
                "usage": usage,
            },
        )

        collected_content = resp_evt.payload.get("content", collected_content)
        collected_tool_calls_raw = resp_evt.payload.get("tool_calls", [])
        collected_tool_calls = [
            tc
            for tc in collected_tool_calls
            if tc.model_dump() in collected_tool_calls_raw
        ]

        return ReasoningResult(
            content=collected_content,
            tool_calls=collected_tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            step=step,
        )

    async def _assemble_messages(self) -> list[ChatMessage]:
        """从 memory 召回上下文，组装 ChatMessage 列表。"""
        recalled = await self.memory.recall(query="", k=64)
        msgs: list[ChatMessage] = [
            ChatMessage(role="system", content=self.system_prompt)
        ]
        for rec in recalled:
            if rec.role == "tool":
                msgs.append(
                    ChatMessage(
                        role="tool",
                        content=rec.content,
                        tool_call_id=rec.meta.get("tool_call_id"),
                        name=rec.meta.get("name"),
                    )
                )
            elif rec.role == "assistant":
                tool_calls_raw = rec.meta.get("tool_calls") or []
                tcs = [ToolCall(**tc) for tc in tool_calls_raw]
                msgs.append(
                    ChatMessage(
                        role="assistant", content=rec.content or None, tool_calls=tcs
                    )
                )
            else:
                msgs.append(ChatMessage(role=rec.role, content=rec.content))

        msgs = await self._apply_sliding_window(msgs)
        return msgs

    async def _apply_sliding_window(
        self, msgs: list[ChatMessage]
    ) -> list[ChatMessage]:
        """滑动窗口：最多缓存 40 次（20 轮），只喂最近 20 次（10 轮）给 LLM。

        从最早开始每 5 轮（10 次）做摘要压缩，只压未压过的段。
        """
        system_msgs = [m for m in msgs if m.role == "system"]
        rest = [m for m in msgs if m.role != "system"]

        if not rest:
            return system_msgs

        # 对未摘要的5轮段依次压缩
        user_indices = [i for i, m in enumerate(rest) if m.role == "user"]
        total_turns = len(user_indices)
        start = self._last_summarized_turn
        while start + self._summarize_every <= total_turns:
            seg_users = user_indices[start : start + self._summarize_every]
            seg = rest[seg_users[0] : seg_users[-1] + 1]
            # 截掉末尾未完成的 tool call 链，确保末尾是最终 assistant 回复
            while seg and (seg[-1].role == "tool" or (seg[-1].role == "assistant" and seg[-1].tool_calls)):
                seg.pop()
            if not seg:
                break
            seg_records = [
                MemoryRecord(role=m.role, content=m.content or "", meta={})
                for m in seg
            ]
            await self.memory.summarize(seg_records)
            start += self._summarize_every
        self._last_summarized_turn = start

        cache_from = self._find_cut_index(rest, self.max_window_turns)
        cached = rest[cache_from:]

        feed_from = self._find_cut_index(cached, self.llm_input_turns)
        return system_msgs + cached[feed_from:]

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
