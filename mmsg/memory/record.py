"""MemoryRecord 数据模型。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MemoryRecord(BaseModel):
    role: str
    content: str
    meta: dict[str, Any] = Field(default_factory=dict)
