"""Agent 核心启动逻辑：_serve / _batch 由 __main__.py 统一调度。"""
from __future__ import annotations

import logging

from .bus.agent import AgentBus
from .bus.message import MESSAGE_INBOUND, SESSION_RESET, MessageBus
from .config import qqbot as _qqbot, workspace_path
from .core import llm_registry, setup_logging, tool_registry
from .llm import OpenAIProvider
from .router import SessionRouter
from .observability import attach_console_sink
from .storage import SqliteStore
from .tools import EchoTool, NowTool
from .transport import run_tcp_server

log = logging.getLogger(__name__)


def _register_plugins() -> None:
    """全局一次性注册插件。只在启动时调用一次。"""
    tool_registry.register("echo")(EchoTool)
    tool_registry.register("now")(NowTool)
    llm_registry.register("openai")(OpenAIProvider)


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

    store = SqliteStore(workspace_path() / "history.db")
    SessionRouter(agent_bus, message_bus, storage=store).install()

    async def on_session_reset(evt) -> None:
        await message_bus.observe(SESSION_RESET, "server", {})

    message_bus.subscribe(SESSION_RESET, on_session_reset)

    await _start_channels(message_bus)

    await run_tcp_server(message_bus, host=host, port=port)


async def _batch(user_input: str) -> None:
    workspace_path().mkdir(parents=True, exist_ok=True)
    setup_logging()
    _register_plugins()

    agent_bus = AgentBus()
    message_bus = MessageBus()
    attach_console_sink(agent_bus, verbose=False)

    store = SqliteStore(workspace_path() / "history.db")
    SessionRouter(agent_bus, message_bus, storage=store).install()

    await message_bus.observe(MESSAGE_INBOUND, "batch", {"text": user_input})
