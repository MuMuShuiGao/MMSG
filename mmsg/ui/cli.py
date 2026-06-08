"""入口：python -m mmsg.ui.cli  或  mmsg（通过 pyproject.scripts）。

TUI 客户端：连接传输服务，只负责展示和输入，Agent 逻辑跑在服务端。
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

from ..core import EventBus, setup_logging
from ..transport import connect_to_server
from .textual.app import ChatApp


def main() -> None:
    load_dotenv()
    setup_logging()

    bus = EventBus()
    # 不在本地创建 AgentLoop，ChatApp 不再持有 agent 引用
    app = ChatApp(bus=bus)

    async def on_ready() -> None:
        """Textual mount 后连接传输服务。"""
        await connect_to_server(bus)

    app._on_transport_ready = on_ready
    app.run()


if __name__ == "__main__":
    main()
