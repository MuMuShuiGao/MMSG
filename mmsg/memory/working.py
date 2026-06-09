"""短期工作记忆：定长环形缓冲区，保留最近若干轮对话原文。"""
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
        # 工作记忆忽略查询内容；按时间顺序返回最近的 k 条记录
        if k <= 0:
            return []
        items = list(self.buf)[-k:]
        return items
