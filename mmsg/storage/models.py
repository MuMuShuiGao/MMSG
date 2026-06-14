from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Session:
    id: str
    source: str = ""
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
    seq: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class TurnRecord:
    """一轮对话中的单条记录，用于落库前收集。"""
    role: str
    content: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class CuriosityNote:
    """主动引擎的好奇心笔记。"""
    content: str
    category: str = "curiosity"                         # follow_up | concern | curiosity
    quality: int = 3                                    # 1-5
    needs_research: bool = False
    status: str = "pending"                             # pending | dismissed | pushed | answered
    id: int | None = None
    session_id: str | None = None
    triggered_at: str | None = None                     # 何时推送给用户的
    merged_from: str | None = None                      # JSON array: 合并来源 note id 列表
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
