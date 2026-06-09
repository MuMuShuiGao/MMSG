"""多行输入栏，带历史和斜杠命令。"""

from textual.events import Key
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

    def _on_key(self, event: Key) -> None:
        # 数字键盘按键在某些终端下 character 为空，手动补上
        if not event.character:
            _numpad: dict[str, str] = {
                "keypad_add": "+", "keypad_subtract": "-",
                "keypad_multiply": "*", "keypad_divide": "/",
                "keypad_decimal": ".",
            }
            for i in range(10):
                _numpad[f"keypad_{i}"] = str(i)
            char = _numpad.get(event.key)
            if char:
                # 直接插入字符并阻止继续传播
                self.insert_text_at_cursor(char)
                event.prevent_default()
                event.stop()
                return
        super()._on_key(event)

    def action_submit(self) -> None:
        text = self.value.strip()
        if not text:
            return
        self._history.append(text)
        self._history_idx = -1
        self._pending = ""
        self.post_message(UserSubmit(text))
        self.clear()
