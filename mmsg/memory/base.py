"""Layered memory contract.

Layer responsibilities:
  - working : last N turns verbatim (this file impl in working.py)
  - episodic: persisted past sessions (future: sqlite)
  - semantic: vector-recalled facts (future: chroma / lancedb)

LayeredMemory composes multiple layers and merges recall results.
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
