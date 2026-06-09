"""SessionRouter：桥接 message_bus ↔ agent_bus。

监听 message.inbound → 创建 AgentLoop → 用 agent_bus 执行 → 将输出 publish 回 message_bus。
同时把 agent_bus 上对外的可观测事件（llm.token, tool.*, agent.final 等）有选择地桥接到 message_bus。
"""

from __future__ import annotations

import logging

from ..bus.agent import LLM_ERROR, LLM_TOKEN, LOOP_STEP, TOOL_CALL, TOOL_ERROR, TOOL_RESULT, AGENT_FINAL, AgentBus
from ..bus.message import MESSAGE_INBOUND, MESSAGE_OUTBOUND, MessageBus

log = logging.getLogger("mmsg.router")

_OBSERVABLE_TYPES = {LLM_TOKEN, LLM_ERROR, LOOP_STEP, TOOL_CALL, TOOL_RESULT, TOOL_ERROR, AGENT_FINAL}


class SessionRouter:
    def __init__(self, agent_bus: AgentBus, message_bus: MessageBus) -> None:
        self._agent_bus = agent_bus
        self._message_bus = message_bus

    def install(self) -> None:
        self._message_bus.subscribe(MESSAGE_INBOUND, self._on_inbound)
        self._agent_bus.subscribe("*", self._bridge_observable)

    async def _on_inbound(self, evt) -> None:
        payload = evt.payload or {}
        text = payload.get("text", "")
        if not text:
            return
        from ..agent import AgentLoop
        from ..core import llm_registry, memory_registry, tool_registry
        from ..memory import LayeredMemory

        llm = llm_registry.create("openai")
        mem = LayeredMemory([memory_registry.create("working", capacity=64)])
        tools = {name: tool_registry.create(name) for name in tool_registry.names()}

        agent = AgentLoop(bus=self._agent_bus, llm=llm, memory=mem, tools=tools)
        result = await agent.run(text)
        await self._message_bus.publish(MESSAGE_OUTBOUND, "router", {"text": result})

    async def _bridge_observable(self, evt) -> None:
        if evt.type in _OBSERVABLE_TYPES:
            await self._message_bus.publish(evt.type, evt.source, evt.payload)
