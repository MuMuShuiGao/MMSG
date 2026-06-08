"""AgentLoop: perceive → think (LLM) → act (tools) → observe → repeat.

Every state transition emits an event on the bus. No hidden side-channels.
"""
from __future__ import annotations

import logging
from typing import Any

from ..core import events as E
from ..core.bus import EventBus
from ..llm.base import ChatMessage, LLMProvider
from ..memory.base import Memory, MemoryRecord
from ..tools.base import Tool

log = logging.getLogger("mmsg.agent")


class AgentLoop:
    def __init__(
        self,
        bus: EventBus,
        llm: LLMProvider,
        memory: Memory,
        tools: dict[str, Tool] | None = None,
        system_prompt: str = "You are a helpful assistant. Use tools when needed.",
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
        await self.bus.publish(
            E.USER_INPUT, self.name, {"text": user_input}
        )
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
                resp = await self.llm.chat(msgs, tools=tool_schemas)
            except Exception as ex:  # surface, don't swallow
                await self.bus.publish(
                    E.LLM_ERROR, self.name, {"step": step, "error": repr(ex)}
                )
                raise

            await self.bus.publish(
                E.LLM_RESPONSE,
                self.name,
                {
                    "step": step,
                    "content": resp.message.content,
                    "tool_calls": [tc.model_dump() for tc in resp.message.tool_calls],
                    "finish_reason": resp.finish_reason,
                    "usage": resp.usage,
                },
            )

            # persist assistant turn (with any tool_calls) into memory
            await self.memory.write(
                MemoryRecord(
                    role="assistant",
                    content=resp.message.content or "",
                    meta={"tool_calls": [tc.model_dump() for tc in resp.message.tool_calls]},
                )
            )

            if not resp.message.tool_calls:
                final_text = resp.message.content or ""
                await self.bus.publish(
                    E.AGENT_FINAL, self.name, {"text": final_text}
                )
                break

            # act
            for tc in resp.message.tool_calls:
                await self.bus.publish(
                    E.TOOL_CALL,
                    self.name,
                    {"step": step, "id": tc.id, "name": tc.name, "arguments": tc.arguments},
                )
                tool = self.tools.get(tc.name)
                if tool is None:
                    result: Any = f"ERROR: tool '{tc.name}' not registered"
                    await self.bus.publish(
                        E.TOOL_ERROR,
                        self.name,
                        {"id": tc.id, "name": tc.name, "error": result},
                    )
                else:
                    try:
                        result = await tool.run(**tc.arguments)
                    except Exception as ex:
                        result = f"ERROR: {ex!r}"
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
            final_text = "[agent stopped: max_steps reached]"
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
