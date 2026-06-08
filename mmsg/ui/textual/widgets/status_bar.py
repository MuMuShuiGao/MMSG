"""底部状态栏：模型名、token 用量、spinner。"""

from rich.text import Text

from textual.widgets import Static

from ..messages import StatusChange


class StatusBar(Static):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.update(Text("Ready", style="dim"))

    def on_status_change(self, msg: StatusChange) -> None:
        t = Text(msg.text, style="dim italic")
        self.update(t)
