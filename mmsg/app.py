"""Agent 核心启动逻辑：_serve / _batch 由 __main__.py 统一调度。"""
from __future__ import annotations

import asyncio
import logging
import sys

from .agent import AgentLoop
from .bus.agent import AgentBus, AgentEvent
from .bus.messagebus import MessageBus
from .config import config_exists, feishu as _feishu, init_config, llm as _llm_cfg, log_level, proactive as _proactive, qqbot as _qqbot, workspace_path
from .core import llm_registry, setup_logging, tool_registry
from .llm import OpenAIProvider
from .memory import create_memory
from .observability import attach_console_sink
from .prompt.segments import SystemPromptBuilder
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


def _ensure_deps() -> None:
    """检查可选依赖是否已安装（不实际加载模块）。"""
    from importlib.util import find_spec

    missing: list[str] = []
    for mod, pkg in [
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn"),
        ("websockets", "websockets"),
        ("lark_oapi", "lark-oapi"),
    ]:
        if find_spec(mod) is None:
            missing.append(pkg)
    if not missing:
        return
    import subprocess

    log.info("正在安装缺失依赖: %s ...", ", ".join(missing))
    subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])


def _build_agent(
    agent_bus: AgentBus,
    message_bus: MessageBus,
    store: SqliteStore,
    llm: OpenAIProvider,
    tools: dict,
    memory: object,
) -> AgentLoop:
    return AgentLoop(
        agent_bus=agent_bus,
        llm=llm,
        memory=memory,
        tools=tools,
        message_bus=message_bus,
        storage=store,
        system_builder=SystemPromptBuilder(workspace=workspace_path()),
    )


async def _start_channels(message_bus: MessageBus) -> list:
    """根据配置按需启动 channel，返回已启动的 channel 列表（供关闭时 stop）。"""
    channels: list = []

    # QQBot
    app_id = _qqbot("app_id")
    secret = _qqbot("secret")
    if app_id and secret:
        from .channel.qqbot import QQBotChannel
        ch = QQBotChannel(app_id=app_id, client_secret=secret, bus=message_bus)
        await ch.start()
        channels.append(ch)

    # Feishu
    fs_app_id = _feishu("app_id")
    fs_secret = _feishu("app_secret")
    if fs_app_id and fs_secret:
        from .channel.feishu import FeishuChannel
        ch = FeishuChannel(app_id=fs_app_id, app_secret=fs_secret, bus=message_bus)
        await ch.start()
        channels.append(ch)

    return channels


def _ensure_config() -> None:
    """确保 config.toml 存在且已填写 api_key。"""
    if not config_exists():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        p = init_config()
        print(f"已生成默认配置文件: {p.resolve()}")
        print("请编辑配置文件中的 api_key 等选项，然后重新运行 mmsg serve")
        sys.exit(0)

    if _llm_cfg("api_key", "") == "sk-your-api-key-here":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print("错误: 请先在 config.toml 中填写正确的 api_key，然后重新运行 mmsg serve")
        sys.exit(1)


async def _serve(host: str, port: int) -> None:
    _ensure_config()
    workspace_path().mkdir(parents=True, exist_ok=True)
    setup_logging(level=log_level())
    _ensure_deps()
    _register_plugins()

    # 静默 Lark SDK WebSocket 正常关闭时的 asyncio task 异常警告
    from websockets.exceptions import ConnectionClosedOK

    def _quiet_asyncio_exception(loop, ctx):
        exc = ctx.get("exception")
        if isinstance(exc, ConnectionClosedOK):
            return
        loop.default_exception_handler(ctx)

    asyncio.get_running_loop().set_exception_handler(_quiet_asyncio_exception)

    agent_bus = AgentBus()
    message_bus = MessageBus()
    attach_console_sink(agent_bus, verbose=False)

    # 共享基础设施
    store = SqliteStore(workspace_path() / "history.db")
    llm = llm_registry.create("openai")
    tools = {name: tool_registry.create(name) for name in tool_registry.names()}
    memory = create_memory()

    agent = _build_agent(agent_bus, message_bus, store, llm, tools, memory)

    async def _bridge_observable(evt) -> None:
        if evt.type in _OBSERVABLE_TYPES:
            await message_bus.events.observe(evt.type, evt.source, evt.payload)

    agent_bus.subscribe("*", _bridge_observable)
    asyncio.create_task(agent.serve())

    # 主动引擎
    proactive = None
    proactive_channel = _proactive("channel", "")
    if proactive_channel:
        from .proactive import ProactiveEngine
        proactive = ProactiveEngine(
            store=store,
            llm=llm,
            memory=memory,
            tools=tools,
            message_bus=message_bus,
            agent_bus=agent_bus,
        )
        asyncio.create_task(proactive.serve())

    # 长期记忆策展 worker
    from .memory.engines.default.curator import MemoryCurator
    memory_curator = MemoryCurator(
        store=store,
        llm=llm,
        markdown=memory.markdown,
    )
    asyncio.create_task(memory_curator.serve())

    channels = await _start_channels(message_bus)

    from .dashboard import start_dashboard
    dashboard_task = asyncio.create_task(
        start_dashboard(agent.storage, agent.memory, host="0.0.0.0", port=9876,
                        proactive_engine=proactive, memory_curator=memory_curator)
    )

    log.info("服务已启动完成")
    try:
        await run_tcp_server(message_bus, host=host, port=port)
    except asyncio.CancelledError:
        pass
    finally:
        log.info("正在关闭服务...")
        # 先停 channel（特别是飞书的 executor 线程）
        for ch in channels:
            try:
                await ch.stop()
            except Exception:
                log.exception("停止 channel 异常: %s", type(ch).__name__)
        # 再取消所有后台任务（stop 后收集，包含 channel 残留 task）
        background_tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for t in background_tasks:
            t.cancel()
        if background_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*background_tasks, return_exceptions=True),
                    timeout=5,
                )
            except asyncio.TimeoutError:
                pass
        log.info("服务已关闭")


async def _batch(user_input: str) -> None:
    _ensure_config()
    workspace_path().mkdir(parents=True, exist_ok=True)
    setup_logging(level=log_level())
    _register_plugins()

    agent_bus = AgentBus()
    message_bus = MessageBus()
    attach_console_sink(agent_bus, verbose=False)

    store = SqliteStore(workspace_path() / "history.db")
    llm = llm_registry.create("openai")
    tools = {name: tool_registry.create(name) for name in tool_registry.names()}
    memory = create_memory()
    agent = _build_agent(agent_bus, message_bus, store, llm, tools, memory)
    final = ""
    async for chunk in agent.run(user_input):
        if chunk.done:
            final = chunk.content
    print(final)
