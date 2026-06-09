"""AgentLoop: 感知 → 思考 (LLM) → 行动 (工具) → 观察 → 重复。

每次状态转换都通过事件总线发布事件，不留隐式侧通道。
"""

from __future__ import annotations

import logging
from typing import Any

from ..bus import agent as E
from ..bus.agent import AgentBus
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

        for step in range(1, self.max_steps + 1):
            await self.bus.publish(E.LOOP_STEP, self.name, {"step": step})
            msgs = await self._assemble_messages()

            await self.bus.publish(
                E.LLM_REQUEST,
                self.name,
                {"step": step, "messages": [m.model_dump() for m in msgs],
                 "tools": [t["function"]["name"] for t in tool_schemas or []]},
            )
            try:
                chunks = self.llm.chat_stream(msgs, tools=tool_schemas)
            except Exception as ex:
                await self.bus.publish(
                    E.LLM_ERROR, self.name, {"step": step, "error": repr(ex)}
                )
                # 不 raise，避免传输层异步任务静默崩溃；错误已通过事件通知客户端
                return f"[错误] LLM 调用失败: {ex!r}"

            collected_content = ""
            collected_tool_calls: list[Any] = []
            finish_reason: str | None = None
            usage: dict[str, Any] = {}
            async for chunk in chunks:
                if chunk.text:
                    collected_content += chunk.text
                    await self.bus.publish(
                        E.LLM_TOKEN, self.name, {"step": step, "text": chunk.text}
                    )
                if chunk.tool_calls:
                    collected_tool_calls = chunk.tool_calls
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                if chunk.usage:
                    usage = chunk.usage

            await self.bus.publish(
                E.LLM_RESPONSE,
                self.name,
                {
                    "step": step,
                    "content": collected_content,
                    "tool_calls": [tc.model_dump() for tc in collected_tool_calls],
                    "finish_reason": finish_reason,
                    "usage": usage,
                },
            )

            # 将 assistant 回合（含工具调用）写入记忆
            await self.memory.write(
                MemoryRecord(
                    role="assistant",
                    content=collected_content or "",
                    meta={"tool_calls": [tc.model_dump() for tc in collected_tool_calls]},
                )
            )

            if not collected_tool_calls:
                final_text = collected_content
                await self.bus.publish(
                    E.AGENT_FINAL, self.name, {"text": final_text}
                )
                break

            # 行动：执行工具调用
            for tc in collected_tool_calls:
                await self.bus.publish(
                    E.TOOL_CALL,
                    self.name,
                    {"step": step, "id": tc.id, "name": tc.name, "arguments": tc.arguments},
                )
                tool = self.tools.get(tc.name)
                if tool is None:
                    result: Any = f"错误: 工具 '{tc.name}' 未注册"
                    await self.bus.publish(
                        E.TOOL_ERROR,
                        self.name,
                        {"id": tc.id, "name": tc.name, "error": result},
                    )
                else:
                    try:
                        result = await tool.run(**tc.arguments)
                    except Exception as ex:
                        result = f"错误: {ex!r}"
                        await self.bus.publish(
                            E.TOOL_ERROR,
                            self.name,
                            {"id": tc.id, "name": tc.name, "error": result},
                        )

                await self.bus.publish(
                    E.TOOL_RESULT,
                    self.name,
                    {"id": tc.id, "name": tc.name, "result": str(result)},
                )
                await self.memory.write(
                    MemoryRecord(
                        role="tool",
                        content=str(result),
                        meta={"tool_call_id": tc.id, "name": tc.name},
                    )
                )
        else:
            final_text = "[agent 已停止: 达到最大步数]"
            await self.bus.publish(
                E.AGENT_FINAL, self.name, {"text": final_text, "reason": "max_steps"}
            )

        await self.bus.publish(E.LOOP_END, self.name, {"final": final_text})
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
