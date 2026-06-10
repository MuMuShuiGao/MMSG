"""可滚动聊天消息流。"""

from rich.text import Text

from textual.containers import VerticalScroll
from textual.widgets import Static

from ..messages import (
    AgentFinal,
    AgentStart,
    ClearScreen,
    ToolCallResult,
    ToolCallStart,
    UserSubmit,
)
from .assistant_msg import AssistantMsg
from .tool_block import ToolBlock
from .user_msg import UserMsg


class ChatLog(VerticalScroll):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._active_assistant: AssistantMsg | None = None
        self._tool_blocks: dict[str, ToolBlock] = {}

    async def on_mount(self) -> None:
        welcome = Text()
        welcome.append("Welcome to MMSG Agent\n", style="bold white")
        welcome.append("Type your message below. ", style="dim")
        welcome.append("/help", style="cyan")
        welcome.append(" for commands.\n\n", style="dim")
        self.mount(Static(welcome))

    def on_user_submit(self, msg: UserSubmit) -> None:
        self.mount(UserMsg(msg.text))
        self.scroll_end(animate=False)

    def on_agent_start(self, msg: AgentStart) -> None:
        self._active_assistant = AssistantMsg()
        self.mount(self._active_assistant)

    def on_tool_call_start(self, msg: ToolCallStart) -> None:
        block = ToolBlock(msg.tool_id, msg.name, msg.arguments)
        self._tool_blocks[msg.tool_id] = block
        self.mount(block)

    def on_tool_call_result(self, msg: ToolCallResult) -> None:
        block = self._tool_blocks.get(msg.tool_id)
        if block:
            block.set_result(msg.result)

    def on_agent_final(self, msg: AgentFinal) -> None:
        self._active_assistant = None
        self.scroll_end(animate=False)

    def on_clear_screen(self, msg: ClearScreen) -> None:
        for child in list(self.children):
            child.remove()
        welcome = Text()
        welcome.append("Cleared.\n", style="dim")
        self.mount(Static(welcome))
