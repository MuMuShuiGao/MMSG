"""Agent 核心启动逻辑：_serve / _batch 由 __main__.py 统一调度。"""
from __future__ import annotations

from dotenv import load_dotenv

from .bus.agent import AgentBus
from .bus.message import MESSAGE_INBOUND, SESSION_RESET, MessageBus
from .core import llm_registry, memory_registry, setup_logging, tool_registry
from .llm import OpenAIProvider
from .memory import WorkingMemory
from .router import SessionRouter
from .observability import attach_console_sink
from .tools import EchoTool, NowTool
from .transport import run_tcp_server


def _register_plugins() -> None:
    """全局一次性注册插件。只在启动时调用一次。"""
    tool_registry.register("echo")(EchoTool)
    tool_registry.register("now")(NowTool)
    llm_registry.register("openai")(OpenAIProvider)
    memory_registry.register("working")(WorkingMemory)


async def _serve(host: str, port: int) -> None:
    load_dotenv()
    setup_logging()
    _register_plugins()

    agent_bus = AgentBus()
    message_bus = MessageBus()
    attach_console_sink(agent_bus, verbose=False)

    SessionRouter(agent_bus, message_bus).install()

    async def on_session_reset(evt) -> None:
        await message_bus.publish(SESSION_RESET, "server", {})

    message_bus.subscribe(SESSION_RESET, on_session_reset)

    await run_tcp_server(message_bus, host=host, port=port)


async def _batch(user_input: str) -> None:
    load_dotenv()
    setup_logging()
    _register_plugins()

    agent_bus = AgentBus()
    message_bus = MessageBus()
    attach_console_sink(agent_bus, verbose=False)

    SessionRouter(agent_bus, message_bus).install()
    await message_bus.publish(MESSAGE_INBOUND, "batch", {"text": user_input})
