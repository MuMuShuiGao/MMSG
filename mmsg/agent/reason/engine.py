"""推理引擎：完整的 ReAct 循环 — LLM 调用 + 流式收集 + 工具执行 + 窗口管理。

think() = 从 memory 读上下文 → 多步推理 & 工具调用 → 写回 memory & 返回记录。
AgentLoop 只负责消息总线消费和组件编排，不问"怎么多轮调工具"。
"""

from __future__ import annotations

import logging
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
    """完整 ReAct 循环结果。"""
    content: str = ""
    records: list[TurnRecord] = Field(default_factory=list)
    steps: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)


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

    async def think(self) -> ThinkingResult:
        """执行完整 ReAct 循环。

        从 memory 读上下文，多步推理 + 工具调用，中间结果写回 memory，
        返回最终文本和 TurnRecord 列表供上层持久化。
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
                break

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

        return ThinkingResult(
            content=final_text, records=records, steps=step, usage=total_usage
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
        return msgs
