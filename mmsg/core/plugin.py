"""插件注册表。插件通过装饰器自注册。类别：llm、tool。
tool 注册表持有实例，支持注册时传构造参数。
"""
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


class ToolRegistry(Registry):
    """持有 Tool 实例的注册表，注册时即可指定构造参数。"""

    def __init__(self) -> None:
        super().__init__("tool")
        self._instances: dict[str, Any] = {}

    def register(self, name: str, **init_kwargs: Any):
        def deco(cls: type[T]) -> type[T]:
            if name in self._items:
                if self._items[name] is cls:
                    return cls
                raise ValueError(f"{self.kind} '{name}' already registered")
            self._items[name] = cls
            self._instances[name] = cls(**init_kwargs)
            return cls
        return deco

    def get(self, name: str):          # 覆盖基类，返回实例
        if name not in self._instances:
            raise KeyError(f"{self.kind} '{name}' not found. have: {list(self._instances)}")
        return self._instances[name]

    def register_instance(self, name: str, instance: Any) -> None:
        """直接注册已构造好的实例（供 MCPManager 等动态注册使用）。"""
        if name in self._instances:
            raise ValueError(f"tool '{name}' already registered")
        self._instances[name] = instance
        self._items[name] = type(instance)

    def all(self) -> dict[str, Any]:
        return dict(self._instances)

    async def aclose(self) -> None:
        for inst in self._instances.values():
            closer = getattr(inst, "aclose", None)
            if closer is not None:
                try:
                    await closer()
                except Exception:
                    pass


llm_registry = Registry("llm")
tool_registry = ToolRegistry()
