"""用户消息气泡。"""

from rich.text import Text

from textual.widgets import Static


class UserMsg(Static):
    def __init__(self, text: str) -> None:
        t = Text()
        t.append("🧑 你\n", style="bold cyan")
        t.append(text)
        super().__init__(t)
