"""插件注册表。插件通过装饰器自注册。类别：llm、tool。"""
from __future__ import annotations

from typing import Any, TypeVar

T = TypeVar("T")


class Registry:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, type] = {}

    def register(self, name: str):
        def deco(cls: type[T]) -> type[T]:
            if name in self._items:
                if self._items[name] is cls:
                    return cls
                raise ValueError(f"{self.kind} '{name}' already registered")
            self._items[name] = cls
            return cls
        return deco

    def get(self, name: str) -> type:
        if name not in self._items:
            raise KeyError(f"{self.kind} '{name}' not found. have: {list(self._items)}")
        return self._items[name]

    def create(self, name: str, **kwargs: Any):
        return self.get(name)(**kwargs)

    def names(self) -> list[str]:
        return list(self._items)


llm_registry = Registry("llm")
tool_registry = Registry("tool")
