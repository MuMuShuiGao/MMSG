"""入口：python -m mmsg.ui.cli  或  mmsg（通过 pyproject.scripts）。

TUI 客户端：连接传输服务，只负责展示和输入，Agent 逻辑跑在服务端。
"""

from __future__ import annotations

from ..bus.eventbus import EventBus
from ..core import setup_logging
from ..transport import connect_to_server
from .textual.app import ChatApp


def main() -> None:
    setup_logging()

    bus = EventBus()
    app = ChatApp(bus=bus)

    async def on_ready() -> None:
        """Textual mount 后连接传输服务。"""
        await connect_to_server(bus)

    app._on_transport_ready = on_ready
    app.run()


if __name__ == "__main__":
    main()
