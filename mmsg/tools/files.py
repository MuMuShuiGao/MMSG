"""文件系统工具：read_file、write_file、list_dir。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from .base import Tool

_MAX_READ_BYTES = 200 * 1024  # 200 KB


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "读取文本文件并带行号返回内容。"
        "二进制文件拒绝读取，最多返回 200 KB。"
        "用 offset（从第几行，1-based）和 limit 读取局部片段。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件的绝对路径或相对路径。"},
            "offset": {"type": "integer", "description": "起始行号，1-based，可选。"},
            "limit": {"type": "integer", "description": "最多读取的行数，可选。"},
        },
        "required": ["path"],
    }

    async def run(self, path: str, offset: int | None = None, limit: int | None = None, **_: Any) -> str:
        p = Path(path).expanduser()
        if not p.exists():
            return f"错误: 文件不存在 '{path}'"
        if not p.is_file():
            return f"错误: 路径不是文件 '{path}'"
        raw = p.read_bytes()
        if b"\x00" in raw[:8192]:
            return f"错误: 二进制文件，拒绝读取 '{path}'"
        if len(raw) > _MAX_READ_BYTES:
            raw = raw[:_MAX_READ_BYTES]
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = max((offset or 1) - 1, 0)
        end = (start + limit) if limit and limit > 0 else len(lines)
        slice_ = lines[start:end]
        return "\n".join(f"{start + i + 1}\t{line}" for i, line in enumerate(slice_))


_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", "__pycache__", "node_modules", ".tox", ".mypy_cache",
    "dist", "build", ".venv", "venv", ".eggs",
})


class ListDirTool(Tool):
    name = "list_dir"
    description = (
        "列出目录的顶层内容。"
        "自动过滤构建产物和版本控制目录。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要列出的目录路径。"},
        },
        "required": ["path"],
    }

    async def run(self, path: str, **_: Any) -> str:
        p = Path(path).expanduser()
        if not p.exists():
            return f"错误: 路径不存在 '{path}'"
        if not p.is_dir():
            return f"错误: 路径不是目录 '{path}'"
        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        lines = []
        for e in entries:
            if e.name in _SKIP_DIRS:
                continue
            lines.append(e.name + ("/" if e.is_dir() else ""))
        return "\n".join(lines) if lines else "(empty)"


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "向工作目录内的文件写入文本内容。"
        "自动创建父目录，文件已存在则覆盖。"
        "工作目录以外的路径一律拒绝。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "相对于工作目录的路径，或工作目录内的绝对路径。",
            },
            "content": {"type": "string", "description": "要写入的文本内容。"},
        },
        "required": ["path", "content"],
    }
    risk: ClassVar[str] = "write"

    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace = workspace

    def _root(self) -> Path:
        if self._workspace is not None:
            return self._workspace.resolve()
        from ..config import workspace_path
        return workspace_path().resolve()

    async def run(self, path: str, content: str, **_: Any) -> str:
        root = self._root()
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = root / target
        target = target.resolve()
        if not target.is_relative_to(root):
            return f"错误: 路径 '{target}' 超出工作目录沙箱 '{root}'"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"已写入 {target} ({len(content)} 字符)"
