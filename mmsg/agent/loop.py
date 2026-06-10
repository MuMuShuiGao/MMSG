"""AgentLoop: 感知 → 思考 (LLM) → 行动 (工具) → 观察 → 重复。

每次状态转换都通过事件总线发布事件，不留隐式侧通道。
"""

from __future__ import annotations

import logging
from typing import Any

from ..bus.agent import AgentEvent, AgentBus
from ..llm.base import ChatMessage, LLMProvider
from ..memory.base import Memory, MemoryRecord
from ..tools.base import Tool

log = logging.getLogger("mmsg.agent")


class AgentLoop:
    def __init__(
        self,
        bus: AgentBus,
        llm: LLMProvider,
        memory: Memory,
        tools: dict[str, Tool] | None = None,
        system_prompt: str = "你是一个有用的助手。需要时请使用工具。",
        max_steps: int = 8,
        name: str = "agent",
    ) -> None:
        self.bus = bus
        self.llm = llm
        self.memory = memory
        self.tools = tools or {}
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.name = name

    async def run(self, user_input: str) -> str:
        await self.memory.write(MemoryRecord(role="user", content=user_input))

        tool_schemas = [t.schema() for t in self.tools.values()] or None
        final_text = ""

        await self.bus.observe(AgentEvent.BeforeTurn, self.name, {})

        for step in range(1, self.max_steps + 1):
            msgs = await self._assemble_messages()

            req_evt = await self.bus.intercept(
                AgentEvent.BeforeStep,
                self.name,
                {"step": step, "messages": [m.model_dump() for m in msgs],
                 "tools": [t["function"]["name"] for t in tool_schemas or []]},
            )
            try:
                chunks = self.llm.chat_stream(msgs, tools=tool_schemas)
            except Exception as ex:
                return f"[错误] LLM 调用失败: {ex!r}"

            collected_content = ""
            collected_tool_calls: list[Any] = []
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
                tc for tc in collected_tool_calls
                if tc.model_dump() in collected_tool_calls_raw
            ]

            await self.memory.write(
                MemoryRecord(
                    role="assistant",
                    content=collected_content or "",
                    meta={"tool_calls": [tc.model_dump() for tc in collected_tool_calls]},
                )
            )

            if not collected_tool_calls:
                final_text = collected_content
                await self.bus.observe(AgentEvent.AfterStep, self.name, {
                    "step": step, "final": True, "text": final_text,
                })
                break

            for tc in collected_tool_calls:
                await self.bus.observe(
                    AgentEvent.BeforeToolCall,
                    self.name,
                    {"step": step, "id": tc.id, "name": tc.name, "arguments": tc.arguments},
                )
                tool = self.tools.get(tc.name)
                if tool is None:
                    result: Any = f"错误: 工具 '{tc.name}' 未注册"
                else:
                    try:
                        result = await tool.run(**tc.arguments)
                    except Exception as ex:
                        result = f"错误: {ex!r}"

                await self.memory.write(
                    MemoryRecord(
                        role="tool",
                        content=str(result),
                        meta={"tool_call_id": tc.id, "name": tc.name},
                    )
                )
                await self.bus.observe(
                    AgentEvent.AfterToolCall,
                    self.name,
                    {"id": tc.id, "name": tc.name, "result": str(result)},
                )

            await self.bus.observe(AgentEvent.AfterStep, self.name, {
                "step": step, "final": False,
            })

        await self.bus.observe(AgentEvent.AfterTurn, self.name, {"final": final_text})
        return final_text

    async def _assemble_messages(self) -> list[ChatMessage]:
        recalled = await self.memory.recall(query="", k=64)
        msgs: list[ChatMessage] = [ChatMessage(role="system", content=self.system_prompt)]
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
                from ..llm.base import ToolCall
                tcs = [ToolCall(**tc) for tc in tool_calls_raw]
                msgs.append(
                    ChatMessage(role="assistant", content=rec.content or None, tool_calls=tcs)
                )
            else:
                msgs.append(ChatMessage(role=rec.role, content=rec.content))  # type: ignore[arg-type]
        return msgs
