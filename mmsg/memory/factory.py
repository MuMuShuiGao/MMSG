"""记忆工厂：根据配置选择后端。"""
from __future__ import annotations

from typing import Any

from ..config import memory_backend as _mem_backend
from .backends import get_backend_factory
from .base import Memory


def create_memory(
    backend: str | None = None,
    config: dict[str, Any] | None = None,
) -> Memory:
    """实例化记忆后端。

    Args:
        backend: 后端名称，默认读 config.toml
        config: 后端特定配置字典
    """
    backend = backend or _mem_backend()
    factory_fn = get_backend_factory(backend)
    return factory_fn(config)
