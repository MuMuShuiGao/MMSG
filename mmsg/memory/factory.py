"""记忆工厂：根据配置选择后端。"""
from __future__ import annotations

import os
from typing import Any

from .backends import get_backend_factory
from .base import Memory


def create_memory(
    backend: str | None = None,
    config: dict[str, Any] | None = None,
) -> Memory:
    """实例化记忆后端。

    Args:
        backend: 后端名称，默认读环境变量 MEMORY_BACKEND
        config: 后端特定配置字典
    """
    backend = backend or os.getenv("MEMORY_BACKEND", "builtin")
    factory_fn = get_backend_factory(backend)
    return factory_fn(config)
