"""助手消息气泡。支持流式逐 token 追加。"""

from rich.text import Text

from textual.widgets import Static


class AssistantMsg(Static):
    def __init__(self) -> None:
        self._text = Text()
        self._text.append("🤖 Agent\n", style="bold green")
        super().__init__(self._text)

    def append_token(self, token: str) -> None:
        self._text.append(token)
        self.update(self._text)
