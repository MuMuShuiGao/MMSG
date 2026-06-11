"""记忆工厂：根据配置选择引擎。"""
from __future__ import annotations

from typing import Any

from ..config import memory_backend as _mem_backend
from .engines import get_engine_factory
from .protocol import Memory


def create_memory(
    backend: str | None = None,
    config: dict[str, Any] | None = None,
) -> Memory:
    return get_engine_factory(backend or _mem_backend())(config)
