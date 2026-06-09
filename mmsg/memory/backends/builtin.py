"""内置记忆后端：组合 WorkingMemory + 未来 episodic/semantic 层。"""
from __future__ import annotations

from typing import Any

from ..base import LayeredMemory, Memory
from ..working import WorkingMemory


def create(config: dict[str, Any] | None = None) -> Memory:
    config = config or {}
    capacity = config.get("working_capacity", 64)
    layers: list[Memory] = [WorkingMemory(capacity=capacity)]
    return LayeredMemory(layers=layers)
