"""插件注册表。插件通过装饰器自注册。类别：llm、tool。
tool 注册表持有实例，支持注册时传构造参数。
"""
from __future__ import annotations

import json
from pathlib import Path
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
        self._disabled: set[str] = set()
        self._sources: dict[str, str] = {}
        self._state_path: Path | None = None

    # ── 注册 ───────────────────────────────────

    def register(self, name: str, **init_kwargs: Any):
        source = init_kwargs.pop("_source", "builtin")
        def deco(cls: type[T]) -> type[T]:
            if name in self._items:
                if self._items[name] is cls:
                    return cls
                raise ValueError(f"{self.kind} '{name}' already registered")
            self._items[name] = cls
            self._instances[name] = cls(**init_kwargs)
            self._sources[name] = source
            return cls
        return deco

    def get(self, name: str):
        if name not in self._instances:
            raise KeyError(f"{self.kind} '{name}' not found. have: {list(self._instances)}")
        return self._instances[name]

    def register_instance(self, name: str, instance: Any, source: str = "") -> None:
        """直接注册已构造好的实例（供 MCPManager 等动态注册使用）。"""
        if name in self._instances:
            raise ValueError(f"tool '{name}' already registered")
        self._instances[name] = instance
        self._items[name] = type(instance)
        self._sources[name] = source

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

    # ── 启用/禁用 ──────────────────────────────

    def is_enabled(self, name: str) -> bool:
        return name not in self._disabled

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """设置工具启用状态。若工具不存在返回 False。"""
        if name not in self._instances:
            return False
        if enabled:
            self._disabled.discard(name)
        else:
            self._disabled.add(name)
        self._save_state()
        return True

    def list_meta(self) -> list[dict[str, Any]]:
        """返回所有已注册工具的元信息列表，供 Dashboard API 使用。"""
        result = []
        for name in self._instances:
            inst = self._instances[name]
            result.append({
                "name": name,
                "description": getattr(inst, "description", ""),
                "risk": getattr(inst, "risk", "safe"),
                "source": self._sources.get(name, ""),
                "enabled": name not in self._disabled,
                "parameters": getattr(inst, "parameters", {}),
            })
        return result

    # ── 持久化 ─────────────────────────────────

    def init_state(self, state_path: Path) -> None:
        self._state_path = state_path
        self._load_state()

    def _load_state(self) -> None:
        if not self._state_path or not self._state_path.is_file():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._disabled = set(data.get("disabled", []))
        except (json.JSONDecodeError, OSError):
            pass

    def _save_state(self) -> None:
        if not self._state_path:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"disabled": sorted(self._disabled)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass


llm_registry = Registry("llm")
tool_registry = ToolRegistry()
