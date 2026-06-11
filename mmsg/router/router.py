"""SessionRouter：桥接 message_bus ↔ agent_bus。

监听 message.inbound → 创建 AgentLoop → 用 agent_bus 执行 → 将输出 publish 回 message_bus。
同时把 agent_bus 上对外的可观测事件（llm.token, tool.*, agent.final 等）有选择地桥接到 message_bus。
"""

from __future__ import annotations

import logging
import uuid

from ..bus.agent import AgentEvent, AgentBus
from ..bus.message import MESSAGE_INBOUND, MESSAGE_OUTBOUND, SESSION_RESET, MessageBus
from ..storage.sqlite import SqliteStore

log = logging.getLogger("mmsg.router")

_OBSERVABLE_TYPES = {AgentEvent.BeforeToolCall, AgentEvent.AfterToolCall, AgentEvent.AfterStep, AgentEvent.AfterTurn}


class SessionRouter:
    def __init__(
        self,
        agent_bus: AgentBus,
        message_bus: MessageBus,
        storage: SqliteStore | None = None,
    ) -> None:
        self._agent_bus = agent_bus
        self._message_bus = message_bus
        self._storage = storage
        self._session_id: str | None = None
        from ..memory import create_memory
        self._memory = create_memory()

    def install(self) -> None:
        self._message_bus.subscribe(MESSAGE_INBOUND, self._on_inbound)
        self._message_bus.subscribe(SESSION_RESET, self._on_session_reset)
        self._agent_bus.subscribe("*", self._bridge_observable)

    async def _on_inbound(self, evt) -> None:
        payload = evt.payload or {}
        text = payload.get("text", "")
        if not text:
            return
        from ..agent import AgentLoop
        from ..core import llm_registry, tool_registry

        llm = llm_registry.create("openai")
        tools = {name: tool_registry.create(name) for name in tool_registry.names()}

        agent = AgentLoop(
            bus=self._agent_bus,
            llm=llm,
            memory=self._memory,
            tools=tools,
            storage=self._storage,
            session_id=self._ensure_session(),
        )
        result = await agent.run(text)

        out_payload: dict = {"text": result}
        if payload.get("openid"):
            out_payload["openid"] = payload["openid"]
            await self._message_bus.observe(MESSAGE_OUTBOUND, evt.source, out_payload)

    async def _bridge_observable(self, evt) -> None:
        if evt.type in _OBSERVABLE_TYPES:
            await self._message_bus.observe(evt.type, evt.source, evt.payload)

    def _ensure_session(self) -> str:
        if self._session_id is None:
            self._session_id = uuid.uuid4().hex[:12]
            if self._storage:
                self._storage.create_session(self._session_id)
            log.info("新会话: %s", self._session_id)
        return self._session_id

    async def _on_session_reset(self, evt) -> None:
        self._session_id = None
