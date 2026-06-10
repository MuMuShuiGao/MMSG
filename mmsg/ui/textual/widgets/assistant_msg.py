"""助手消息气泡。"""
from rich.text import Text
from textual.widgets import Static


class AssistantMsg(Static):
    def __init__(self) -> None:
        self._text = Text()
        self._text.append("🤖 Agent\n", style="bold green")
        super().__init__(self._text)
