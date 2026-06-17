"""MCPTool: 把单个 MCP tool 适配成 mmsg Tool 实例。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..base import Tool

if TYPE_CHECKING:
    from .client import MCPClient


_DESTRUCTIVE_RISK = "write"
_DEFAULT_RISK = "network"
_RISK_ORDER = {"safe": 0, "write": 1, "network": 2}


def _effective_risk(configured: str, annotations: Any) -> str:
    """若 tool 的 annotations.destructiveHint=True，强制升至 write。"""
    risk = configured if configured in _RISK_ORDER else _DEFAULT_RISK
    if annotations is not None:
        destructive = getattr(annotations, "destructiveHint", None)
        if destructive is True:
            if _RISK_ORDER.get(risk, 0) < _RISK_ORDER[_DESTRUCTIVE_RISK]:
                risk = _DESTRUCTIVE_RISK
    return risk


class MCPTool(Tool):
    """把一个 MCP server 的单个 tool 包装成 mmsg Tool。

    name 格式：mcp__<server>__<tool_name>
    """

    def __init__(
        self,
        *,
        server: str,
        mcp_tool: Any,
        client: MCPClient,
        configured_risk: str = _DEFAULT_RISK,
        timeout: float = 30.0,
    ) -> None:
        self._raw_name: str = mcp_tool.name
        self._client = client
        self._timeout = timeout

        self.name: str = f"mcp__{server}__{mcp_tool.name}"
        self.description: str = mcp_tool.description or ""
        self.parameters: dict[str, Any] = (
            mcp_tool.inputSchema
            if isinstance(mcp_tool.inputSchema, dict)
            else {"type": "object", "properties": {}}
        )
        annotations = getattr(mcp_tool, "annotations", None)
        self.risk: str = _effective_risk(configured_risk, annotations)

    async def run(self, **kwargs: Any) -> str:
        result = await self._client.call_tool(
            self._raw_name, arguments=kwargs, timeout=self._timeout
        )
        return self._extract_text(result)

    @staticmethod
    def _extract_text(result: Any) -> str:
        """从 CallToolResult.content 提取文本，其他类型转占位。"""
        if result is None:
            return ""
        contents = getattr(result, "content", None)
        if contents is None:
            return str(result)
        parts: list[str] = []
        for item in contents:
            item_type = getattr(item, "type", None)
            if item_type == "text":
                parts.append(item.text or "")
            elif item_type == "image":
                mime = getattr(item, "mimeType", "unknown")
                parts.append(f"[image: {mime}]")
            else:
                parts.append(f"[{item_type or 'resource'}]")
        return "\n".join(parts)
