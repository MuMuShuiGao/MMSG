"""记忆引擎注册表。"""
from __future__ import annotations

from typing import Callable

from ..protocol import Memory

ENGINE_REGISTRY: dict[str, str] = {
    "default": "mmsg.memory.engines.default.engine",
}


def get_engine_factory(name: str) -> Callable[..., Memory]:
    import importlib

    module_path = ENGINE_REGISTRY.get(name)
    if module_path is None:
        raise ValueError(f"未知记忆引擎 '{name}'，可用: {list(ENGINE_REGISTRY)}")
    mod = importlib.import_module(module_path)
    return mod.create
