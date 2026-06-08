"""ChatApp：MMSG Agent 的 Textual 用户界面。

不直接持有 AgentLoop，所有 agent 通信经由传输层 (EventBus → transport.raw)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine

from textual.app import App

from ...core.bus import EventBus
from .bridge import BusBridge
from .commands import handle_command
from .messages import UserSubmit
from .widgets import ChatLog, InputBar, StatusBar


class ChatApp(App):
    CSS_PATH = "theme.tcss"

    BINDINGS = [
        ("ctrl+c", "cancel_or_exit", "取消 / 退出"),
        ("escape", "focus_input", "聚焦输入"),
    ]

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self._mmsg_bus = bus
        self._task: asyncio.Task | None = None
        # 由 cli.py 在 mount 前注入，用于连接传输层
        self._on_transport_ready: Callable[[], Coroutine] | None = None

    def compose(self):
        yield ChatLog(id="log")
        yield InputBar(id="input")
        yield StatusBar(id="status")

    async def on_mount(self) -> None:
        BusBridge(self._mmsg_bus, self).install()
        if self._on_transport_ready:
            self._task = asyncio.create_task(self._on_transport_ready())
        self.query_one(InputBar).focus()

    async def on_user_submit(self, msg: UserSubmit) -> None:
        text = msg.text.strip()
        if handle_command(text, self):
            return

        # 通过传输层发送 user.input 到服务端
        await self._mmsg_bus.publish("user.input", "ui", {"text": text})

    def action_cancel_or_exit(self) -> None:
        """如果后台任务在运行则取消，否则退出应用。"""
        if self._task and not self._task.done():
            self._task.cancel()
        else:
            self.exit()

    def action_focus_input(self) -> None:
        self.query_one(InputBar).focus()
