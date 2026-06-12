"""知识库 — 管理 memory.md，负责长期知识/事实的读写。"""
from __future__ import annotations

from pathlib import Path


class KnowledgeBase:
    def __init__(self, file_path: Path) -> None:
        self._path = file_path
        if not self._path.exists():
            self._path.write_text("", encoding="utf-8")

    def read(self) -> str | None:
        content = self._path.read_text(encoding="utf-8").strip()
        return content or None

    def write(self, content: str) -> None:
        self._path.write_text(content, encoding="utf-8")
