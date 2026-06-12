"""上下文窗口 — 管理 current_context.md，负责近期摘要的读写。"""
from __future__ import annotations

from pathlib import Path


class ContextWindow:
    def __init__(self, file_path: Path, max_turns: int = 5) -> None:
        self._path = file_path
        if not self._path.exists():
            self._path.write_text("# 近期摘要\n", encoding="utf-8")

    def read(self) -> str | None:
        content = self._path.read_text(encoding="utf-8").strip()
        return content or None

    def write(self, content: str) -> None:
        self._path.write_text(content, encoding="utf-8")
