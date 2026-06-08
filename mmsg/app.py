"""Demo entry point. Run:  python -m mmsg.app  "what time is it?" """
from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

from .agent import AgentLoop
from .core import EventBus, llm_registry, memory_registry, setup_logging, tool_registry
from .llm import OpenAIProvider
from .memory import LayeredMemory, WorkingMemory
from .observability import attach_console_sink
from .tools import EchoTool, NowTool


async def main(user_input: str) -> None:
    load_dotenv()
    setup_logging()

    # --- explicit plugin registration ---
    tool_registry.register("echo")(EchoTool)
    tool_registry.register("now")(NowTool)
    llm_registry.register("openai")(OpenAIProvider)
    memory_registry.register("working")(WorkingMemory)
    # ----------------------------------

    bus = EventBus()
    attach_console_sink(bus, verbose=False)

    llm = llm_registry.create("openai")
    mem = LayeredMemory([memory_registry.create("working", capacity=64)])
    tools = {name: tool_registry.create(name) for name in tool_registry.names()}

    agent = AgentLoop(bus=bus, llm=llm, memory=mem, tools=tools)
    await agent.run(user_input)


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "What time is it now? Use the now tool."
    asyncio.run(main(q))
