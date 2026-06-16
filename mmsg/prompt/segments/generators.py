"""Segment builders：每个函数返回一段完整 prompt 文本。

静态段直接引用 constants.py 中的常量；
动态段（如 workspace）接受参数并在函数内插值。
"""

from __future__ import annotations

from pathlib import Path

from .constants import (
    MMSG_BEHAVIOR_TEXT,
    MMSG_IDENTITY_TEXT,
    MMSG_OUTPUT_FORMAT_TEXT,
    MMSG_TOOL_USAGE_TEXT,
)


def build_identity() -> str:
    """角色定义与人格（静态）。"""
    return MMSG_IDENTITY_TEXT


def build_behavior() -> str:
    """行为规范与回复原则（静态）。"""
    return MMSG_BEHAVIOR_TEXT


def build_tool_usage() -> str:
    """工具使用规范（静态）。"""
    return MMSG_TOOL_USAGE_TEXT


def build_output_format() -> str:
    """输出格式规范（静态）。"""
    return MMSG_OUTPUT_FORMAT_TEXT


def build_workspace(workspace: Path) -> str:
    """工作区路径与文件索引（动态）。"""
    root = str(workspace.expanduser().resolve())
    return f"""\
## 工作区

- 根目录：{root}
- 长期记忆：{root}/memory/MEMORY.md
- 近期语境摘要：{root}/memory/RECENT_CONTEXT.md
"""
