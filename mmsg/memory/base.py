"""分层记忆协议。

各层职责：
  - working  : 最近 N 轮对话原文（实现在 working.py）
  - episodic : 持久化历史会话（计划：sqlite）
  - semantic : 向量检索的事实知识（计划：chroma / lancedb）

LayeredMemory 组合多层记忆并合并召回结果。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class MemoryRecord(BaseModel):
    role: str
    content: str
    meta: dict[str, Any] = Field(default_factory=dict)


class Memory(ABC):
    layer: str = "abstract"

    @abstractmethod
    async def write(self, record: MemoryRecord) -> None: ...

    @abstractmethod
    async def recall(self, query: str, k: int = 8) -> list[MemoryRecord]: ...


class LayeredMemory(Memory):
    layer = "layered"

    def __init__(self, layers: list[Memory]) -> None:
        self.layers = layers

    async def write(self, record: MemoryRecord) -> None:
        for layer in self.layers:
            await layer.write(record)

    async def recall(self, query: str, k: int = 8) -> list[MemoryRecord]:
        merged: list[MemoryRecord] = []
        seen: set[tuple[str, str]] = set()
        for layer in self.layers:
            for rec in await layer.recall(query, k):
                key = (rec.role, rec.content)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(rec)
        return merged
