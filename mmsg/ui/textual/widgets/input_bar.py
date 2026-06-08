"""多行输入栏，带历史和斜杠命令。"""

from textual.widgets import Input
from textual.binding import Binding

from ..messages import UserSubmit


class InputBar(Input):
    BINDINGS = [
        Binding("enter", "submit", "Send", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(placeholder="Type your message... /help for commands", **kwargs)
        self._history: list[str] = []
        self._history_idx: int = -1
        self._pending: str = ""

    def action_submit(self) -> None:
        text = self.value.strip()
        if not text:
            return
        self._history.append(text)
        self._history_idx = -1
        self._pending = ""
        self.post_message(UserSubmit(text))
        self.clear()
