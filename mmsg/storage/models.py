from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Session:
    id: str
    title: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Message:
    session_id: str
    role: str
    content: str = ""
    meta: dict = field(default_factory=dict)
    id: int | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class TurnRecord:
    """一轮对话中的单条记录，用于落库前收集。"""
    role: str
    content: str = ""
    meta: dict = field(default_factory=dict)
