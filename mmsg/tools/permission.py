"""权限门卫：BeforeToolCall 拦截器。

每个 Tool 通过 risk 类属性声明风险等级（safe | write | network）。
门卫对照允许集合检查等级，拦截时向事件 payload 注入
``denied=True`` + ``deny_reason``。

v1 默认：三种风险等级全部放行。
如需收窄，子类覆盖 ``_ALLOW`` 即可。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..bus.eventbus import Event

if TYPE_CHECKING:
    from .base import Tool


class PermissionGate:
    """通过 ``agent_bus.subscribe_intercept(AgentEvent.BeforeToolCall, gate)`` 挂载。"""

    _ALLOW: frozenset[str] = frozenset({"safe", "write", "network"})

    def __init__(self, tools: dict[str, "Tool"]) -> None:
        self._tools = tools

    async def __call__(self, evt: Event) -> Event:
        name: str = evt.payload.get("name", "")
        tool = self._tools.get(name)
        if tool is None:
            return evt
        risk: str = getattr(type(tool), "risk", "safe")
        if risk not in self._ALLOW:
            return evt.model_copy(
                update={
                    "payload": {
                        **evt.payload,
                        "denied": True,
                        "deny_reason": f"风险等级 '{risk}' 未被允许",
                    }
                }
            )
        return evt
