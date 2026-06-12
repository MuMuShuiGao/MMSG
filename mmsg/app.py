"""Agent 核心启动逻辑：_serve / _batch 由 __main__.py 统一调度。"""
from __future__ import annotations

import asyncio
import logging

from .agent import AgentLoop
from .bus.agent import AgentBus, AgentEvent
from .bus.messagebus import MessageBus
from .config import qqbot as _qqbot, workspace_path
from .core import llm_registry, setup_logging, tool_registry
from .llm import OpenAIProvider
from .memory import create_memory
from .observability import attach_console_sink
from .storage import SqliteStore
from .tools import EchoTool, NowTool
from .transport import run_tcp_server

log = logging.getLogger(__name__)

_OBSERVABLE_TYPES = {
    AgentEvent.BeforeToolCall,
    AgentEvent.AfterToolCall,
    AgentEvent.AfterStep,
    AgentEvent.AfterTurn,
}


def _register_plugins() -> None:
    """全局一次性注册插件。只在启动时调用一次。"""
    tool_registry.register("echo")(EchoTool)
    tool_registry.register("now")(NowTool)
    llm_registry.register("openai")(OpenAIProvider)


def _build_agent(agent_bus: AgentBus, message_bus: MessageBus) -> AgentLoop:
    store = SqliteStore(workspace_path() / "history.db")
    llm = llm_registry.create("openai")
    tools = {name: tool_registry.create(name) for name in tool_registry.names()}
    memory = create_memory()
    return AgentLoop(
        agent_bus=agent_bus,
        llm=llm,
        memory=memory,
        tools=tools,
        message_bus=message_bus,
        storage=store,
    )


async def _start_channels(message_bus: MessageBus) -> None:
    """根据配置按需启动 channel。每个 channel 自行决定是否可用。"""
    app_id = _qqbot("app_id")
    secret = _qqbot("secret")
    if app_id and secret:
        try:
            from .channel.qqbot import QQBotChannel
            ch = QQBotChannel(app_id=app_id, client_secret=secret, bus=message_bus)
            await ch.start()
        except ImportError:
            log.warning("websockets not installed, QQBot channel disabled")


async def _serve(host: str, port: int) -> None:
    workspace_path().mkdir(parents=True, exist_ok=True)
    setup_logging()
    _register_plugins()

    agent_bus = AgentBus()
    message_bus = MessageBus()
    attach_console_sink(agent_bus, verbose=False)

    agent = _build_agent(agent_bus, message_bus)

    async def _bridge_observable(evt) -> None:
        if evt.type in _OBSERVABLE_TYPES:
            await message_bus.events.observe(evt.type, evt.source, evt.payload)

    agent_bus.subscribe("*", _bridge_observable)
    asyncio.create_task(agent.serve())

    await _start_channels(message_bus)

    from .dashboard import start_dashboard
    dashboard_task = asyncio.create_task(
        start_dashboard(agent.storage, agent.memory, host="0.0.0.0", port=9876)
    )

    await run_tcp_server(message_bus, host=host, port=port)


async def _batch(user_input: str) -> None:
    workspace_path().mkdir(parents=True, exist_ok=True)
    setup_logging()
    _register_plugins()

    agent_bus = AgentBus()
    message_bus = MessageBus()
    attach_console_sink(agent_bus, verbose=False)

    agent = _build_agent(agent_bus, message_bus)
    final = ""
    async for chunk in agent.run(user_input):
        if chunk.done:
            final = chunk.content
    print(final)
