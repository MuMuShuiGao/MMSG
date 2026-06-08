"""可折叠的工具调用面板。"""

import json

from rich.panel import Panel
from rich.text import Text

from textual.widgets import Static


class ToolBlock(Static):
    def __init__(self, tool_id: str, name: str, arguments: dict) -> None:
        self.tool_id = tool_id
        self._header = Text()
        self._header.append("⚙ ", style="magenta")
        self._header.append(f"{name}", style="bold magenta")
        self._header.append("  ")
        self._header.append(json.dumps(arguments, ensure_ascii=False), style="dim")

        self._body: list[Text] = []
        super().__init__(self._header)

    def set_result(self, result: str) -> None:
        t = Text()
        t.append("  → ", style="green")
        # 截断过长结果
        if len(result) > 300:
            result = result[:300] + "..."
        t.append(result, style="dim")
        self._body.append(t)
        self._render()

    def set_error(self, error: str) -> None:
        t = Text()
        t.append("  ✗ ", style="red")
        t.append(error, style="red dim")
        self._body.append(t)
        self._render()

    def _render(self) -> None:
        content = self._header.copy()
        for b in self._body:
            content.append("\n")
            content.append(b)
        self.update(Panel(content, border_style="magenta", padding=(0, 1)))
