"""上下文窗口 — 管理 current_context.md，负责对话轮次读写与裁剪。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


class ContextWindow:
    def __init__(self, file_path: Path, max_turns: int = 5) -> None:
        self._path = file_path
        self.max_turns = max_turns
        self._latest_summary: str | None = None
        if not self._path.exists():
            self._path.write_text("# 近期摘要\n\n# 最近对话\n\n", encoding="utf-8")
        self._latest_summary = self._load_summary()

    def _load_summary(self) -> str | None:
        text = self._path.read_text(encoding="utf-8")
        idx = text.find("# 最近对话")
        if idx == -1:
            return None
        summary_section = text[:idx].strip()
        if summary_section == "# 近期摘要":
            return None
        last = summary_section.split("### [")
        if len(last) < 2:
            return None
        return ("### [" + last[-1]).strip()

    async def start_turn(self) -> None:
        with open(self._path, "a", encoding="utf-8") as f:
            f.write("---\n")

    async def write(self, role: str, content: str) -> None:
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(f"**{role}**: {content}\n")

    def read(self) -> str | None:
        content = self._path.read_text(encoding="utf-8").strip()
        return content or None

    async def end_turn(self, summary: str = "") -> None:
        if summary:
            ts = datetime.now(timezone.utc).strftime("%m-%d %H:%M")
            self._latest_summary = f"### [{ts}]\n{summary}"
        self._rebuild()

    def _rebuild(self) -> None:
        text = self._path.read_text(encoding="utf-8")
        idx = text.find("# 最近对话")
        turns_raw = ""
        if idx != -1:
            turns_raw = text[idx + len("# 最近对话"):].lstrip("\n")

        blocks = [b.strip() for b in turns_raw.split("---\n") if b.strip()]
        if len(blocks) > self.max_turns:
            blocks = blocks[-self.max_turns:]

        summary_text = ("# 近期摘要\n\n" + self._latest_summary) if self._latest_summary else "# 近期摘要\n"
        turns_text = "# 最近对话\n" + "\n---\n".join(blocks) + "\n" if blocks else "# 最近对话\n"
        self._path.write_text(summary_text + "\n" + turns_text, encoding="utf-8")
