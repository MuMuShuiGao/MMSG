"""Memory 抽象协议 — 上游唯一依赖。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .record import MemoryRecord


class Memory(ABC):
    layer: str = "abstract"

    @abstractmethod
    async def write(self, record: MemoryRecord) -> None: ...

    @abstractmethod
    async def recall(self, query: str, k: int = 8) -> list[MemoryRecord]: ...

    async def summarize(self, messages: list[MemoryRecord]) -> None:
        pass

    async def start_turn(self) -> None:
        pass

    async def end_turn(self, user_input: str = "", assistant_output: str = "") -> None:
        pass
