"""入口：服务端模式  python -m mmsg.app --serve  或  单次批处理模式  python -m mmsg.app "问题" """
from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv

from .agent import AgentLoop
from .core import EventBus, llm_registry, memory_registry, setup_logging, tool_registry
from .core import events as E
from .llm import OpenAIProvider
from .memory import LayeredMemory, WorkingMemory
from .observability import attach_console_sink
from .tools import EchoTool, NowTool
from .transport import run_tcp_server


def _build_agent(bus: EventBus) -> AgentLoop:
    """组装 AgentLoop 及其所有依赖。"""
    tool_registry.register("echo")(EchoTool)
    tool_registry.register("now")(NowTool)
    llm_registry.register("openai")(OpenAIProvider)
    memory_registry.register("working")(WorkingMemory)

    llm = llm_registry.create("openai", model="deepseek-v4-pro")
    mem = LayeredMemory([memory_registry.create("working", capacity=64)])
    tools = {name: tool_registry.create(name) for name in tool_registry.names()}

    return AgentLoop(bus=bus, llm=llm, memory=mem, tools=tools)


async def _serve(host: str, port: int) -> None:
    """以服务端模式运行：启动传输监听，等待 TUI 客户端连接。"""
    load_dotenv()
    setup_logging()

    bus = EventBus()
    attach_console_sink(bus, verbose=False)
    # 初始化插件，这样后续每个会话的 _build_agent 都能用已注册的插件
    _build_agent(bus)

    async def on_user_input(evt) -> None:
        """客户端发来 user.input → 新建 AgentLoop 处理本轮对话。"""
        agent = _build_agent(bus)
        await agent.run(evt.payload.get("text", ""))

    bus.subscribe(E.USER_INPUT, on_user_input)

    # 客户端请求清空会话时，底层 memory 由 agent.run 每次新建实例自然刷新；
    # session.reset 事件用于通知 UI 清屏，也在此记录日志
    async def on_session_reset(evt) -> None:
        from .core import events as E2
        await bus.publish(E2.SESSION_RESET, "server", {})

    bus.subscribe(E.SESSION_RESET, on_session_reset)

    await run_tcp_server(bus, host=host, port=port)


async def _batch(user_input: str) -> None:
    """单次批处理模式：无网络，直接运行一次 agent 循环。"""
    load_dotenv()
    setup_logging()

    bus = EventBus()
    attach_console_sink(bus, verbose=False)

    agent = _build_agent(bus)
    await agent.run(user_input)


def main() -> None:
    parser = argparse.ArgumentParser(description="MMSG Agent")
    parser.add_argument("--serve", action="store_true", help="以传输服务模式启动")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=9090, help="监听端口（默认 9090）")
    parser.add_argument("query", nargs="*", help="批处理模式下要发送的问题")
    args = parser.parse_args()

    if args.serve:
        asyncio.run(_serve(args.host, args.port))
    else:
        q = " ".join(args.query) or "现在是几点？用 now 工具。"
        asyncio.run(_batch(q))


if __name__ == "__main__":
    main()
