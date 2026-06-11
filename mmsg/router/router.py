"""SessionRouter：桥接 message_bus ↔ agent_bus。

consume_inbound() → 创建 AgentLoop → 用 agent_bus 执行 → publish_outbound()。
同时把 agent_bus 上对外的可观测事件有选择地桥接到 message_bus.events。
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from ..bus.agent import AgentEvent, AgentBus
from ..bus.messagebus import MessageBus, SESSION_RESET
from ..storage.sqlite import SqliteStore

log = logging.getLogger("mmsg.router")

_OBSERVABLE_TYPES = {
    AgentEvent.BeforeToolCall,
    AgentEvent.AfterToolCall,
    AgentEvent.AfterStep,
    AgentEvent.AfterTurn,
}


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
        self._task: asyncio.Task | None = None
        from ..memory import create_memory
        self._memory = create_memory()

    def install(self) -> None:
        self._agent_bus.subscribe("*", self._bridge_observable)
        self._message_bus.events.subscribe(SESSION_RESET, self._on_session_reset)
        self._task = asyncio.create_task(self._consume_loop())

    async def run_once(self, text: str) -> str:
        """直接执行一次（batch 模式），不走队列。"""
        return await self._process("batch", text)

    # ── 消费循环 ──────────────────────────────────────

    async def _consume_loop(self) -> None:
        while True:
            item = await self._message_bus.consume_inbound()
            payload = item.payload or {}
            text = payload.get("text", "")
            if not text:
                continue
            result = await self._process(item.source, text)

            out_payload: dict = {"text": result}
            if payload.get("openid"):
                out_payload["openid"] = payload["openid"]
            await self._message_bus.publish_outbound(item.source, out_payload)

    async def _process(self, source: str, text: str) -> str:
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
        return await agent.run(text)

    # ── 可观测事件桥接 ────────────────────────────────

    async def _bridge_observable(self, evt) -> None:
        if evt.type in _OBSERVABLE_TYPES:
            await self._message_bus.events.observe(evt.type, evt.source, evt.payload)

    # ── 会话管理 ──────────────────────────────────────

    def _ensure_session(self) -> str:
        if self._session_id is None:
            self._session_id = uuid.uuid4().hex[:12]
            if self._storage:
                self._storage.create_session(self._session_id)
            log.info("新会话: %s", self._session_id)
        return self._session_id

    async def _on_session_reset(self, evt) -> None:
        self._session_id = None
