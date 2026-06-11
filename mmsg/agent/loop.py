"""AgentLoop: 消费消息队列 → 感知 → 思考 (LLM) → 行动 (工具) → 观察 → 重复。

长驻 serve() 循环从 MessageBus 取用户输入，每次输入执行一个 turn。
每次状态转换都通过 agent_bus 发布事件，不留隐式侧通道。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ..bus.agent import AgentEvent, AgentBus
from ..bus.messagebus import MessageBus, SESSION_RESET
from ..llm.base import ChatMessage, LLMProvider
from ..memory import Memory, MemoryRecord
from ..tools.base import Tool
from ..storage.models import Message, TurnRecord
from ..storage.sqlite import SqliteStore

log = logging.getLogger("mmsg.agent")


class AgentLoop:
    def __init__(
        self,
        agent_bus: AgentBus,
        llm: LLMProvider,
        memory: Memory,
        tools: dict[str, Tool] | None = None,
        message_bus: MessageBus | None = None,
        system_prompt: str = "你是一个有用的助手。需要时请使用工具。",
        max_steps: int = 8,
        name: str = "agent",
        storage: SqliteStore | None = None,
    ) -> None:
        self.bus = agent_bus
        self.llm = llm
        self.memory = memory
        self.tools = tools or {}
        self._message_bus = message_bus
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.name = name
        self.storage = storage
        self._session_id: str | None = None

    # ── 消息队列消费 ──────────────────────────────

    async def serve(self) -> None:
        """长驻循环：从 message_bus 消费入站消息 → run() → 发布出站。"""
        if self._message_bus is None:
            raise RuntimeError("serve() 需要 message_bus")
        self._message_bus.events.subscribe(SESSION_RESET, self._on_session_reset)
        while True:
            item = await self._message_bus.consume_inbound()
            payload = item.payload or {}
            text = payload.get("text", "")
            if not text:
                continue
            result = await self.run(text)

            out_payload: dict = {"text": result}
            if payload.get("openid"):
                out_payload["openid"] = payload["openid"]
            await self._message_bus.publish_outbound(item.source, out_payload)

    # ── 会话管理 ──────────────────────────────────

    def _ensure_session(self) -> str:
        if self._session_id is None:
            self._session_id = uuid.uuid4().hex[:12]
            if self.storage:
                self.storage.create_session(self._session_id)
            log.info("新会话: %s", self._session_id)
        return self._session_id

    async def _on_session_reset(self, evt: Any) -> None:
        self._session_id = None

    async def run(self, user_input: str) -> str:
        self._ensure_session()
        turn_records: list[TurnRecord] = []
        await self.memory.start_turn()

        user_record = MemoryRecord(role="user", content=user_input)
        await self.memory.write(user_record)
        turn_records.append(TurnRecord(role="user", content=user_input))

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

            assistant_record = MemoryRecord(
                role="assistant",
                content=collected_content or "",
                meta={"tool_calls": [tc.model_dump() for tc in collected_tool_calls]},
            )
            await self.memory.write(assistant_record)
            turn_records.append(
                TurnRecord(
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

                tool_record = MemoryRecord(
                    role="tool",
                    content=str(result),
                    meta={"tool_call_id": tc.id, "name": tc.name},
                )
                await self.memory.write(tool_record)
                turn_records.append(
                    TurnRecord(
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

        await self._persist_turn(turn_records)
        await self.memory.end_turn(user_input, final_text[:200])
        await self.bus.observe(AgentEvent.AfterTurn, self.name, {"final": final_text})
        return final_text

    async def _persist_turn(self, records: list[TurnRecord]) -> None:
        if not self.storage or not self._session_id:
            return
        for rec in records:
            self.storage.save_message(
                Message(
                    session_id=self._session_id,
                    role=rec.role,
                    content=rec.content,
                    meta=rec.meta,
                )
            )

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
