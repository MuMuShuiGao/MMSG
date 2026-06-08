"""Short-term working memory: bounded ring buffer of recent turns."""
from __future__ import annotations

from collections import deque

from .base import Memory, MemoryRecord


class WorkingMemory(Memory):
    layer = "working"

    def __init__(self, capacity: int = 32) -> None:
        self.buf: deque[MemoryRecord] = deque(maxlen=capacity)

    async def write(self, record: MemoryRecord) -> None:
        self.buf.append(record)

    async def recall(self, query: str, k: int = 8) -> list[MemoryRecord]:
        # working memory ignores query; returns most-recent k in chronological order
        if k <= 0:
            return []
        items = list(self.buf)[-k:]
        return items
