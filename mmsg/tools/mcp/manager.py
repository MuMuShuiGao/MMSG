"""MCPManager: 多 MCP server 编排、注册、生命周期管理。

- 启动期并发连接所有 enabled server
- 失败者跳过 + 告警，后台指数退避重连
- 连接成功后把每个 MCP tool 注册到 tool_registry
- aclose() 关闭全部连接
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .adapter import MCPTool
from .client import MCPClient, MCPConnectionError

if TYPE_CHECKING:
    from ...core.plugin import ToolRegistry

log = logging.getLogger("mmsg.mcp.manager")

_MAX_BACKOFF = 300.0   # 最长重试间隔 5 分钟
_BASE_BACKOFF = 5.0    # 初始重试间隔


class MCPManager:
    def __init__(
        self,
        registry: "ToolRegistry",
        workspace: Path | None = None,
    ) -> None:
        self._registry = registry
        self._workspace = workspace
        self._clients: dict[str, MCPClient] = {}
        self._configs: dict[str, dict[str, Any]] = {}
        self._reconnect_tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        """读配置，并发连接所有 enabled server。"""
        from ...config import mcp_servers
        servers = mcp_servers()
        if not servers:
            log.debug("未配置 MCP server，跳过")
            return

        tasks = [
            self._connect_server(name, cfg)
            for name, cfg in servers.items()
            if cfg.get("enabled", True)
        ]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, BaseException):
                    log.warning("MCP server 启动异常（未预期）: %s", r)

    async def _connect_server(self, name: str, cfg: dict[str, Any]) -> None:
        """尝试连接单个 server；失败告警并安排重连。"""
        self._configs[name] = cfg
        client = self._make_client(name, cfg)
        try:
            await client.connect()
        except Exception as exc:
            log.warning("MCP server '%s' 连接失败: %s，将在后台重试", name, exc)
            self._schedule_reconnect(name)
            return

        self._clients[name] = client
        await self._register_tools(name, client, cfg)

    def _make_client(self, name: str, cfg: dict[str, Any]) -> MCPClient:
        transport = cfg.get("transport", "stdio")
        return MCPClient(
            name=name,
            transport=transport,
            command=cfg.get("command"),
            args=cfg.get("args", []),
            env=cfg.get("env"),
            cwd=self._workspace,
            url=cfg.get("url"),
            headers=cfg.get("headers"),
        )

    async def _register_tools(
        self, name: str, client: MCPClient, cfg: dict[str, Any], *, is_reconnect: bool = False
    ) -> None:
        try:
            mcp_tools = await client.list_tools()
        except MCPConnectionError as exc:
            log.warning("MCP server '%s' list_tools 失败: %s", name, exc)
            return

        risk = cfg.get("risk", "network")
        timeout = float(cfg.get("timeout", 30.0))
        registered = 0
        for mcp_tool in mcp_tools:
            tool = MCPTool(
                server=name,
                mcp_tool=mcp_tool,
                client=client,
                configured_risk=risk,
                timeout=timeout,
            )
            if is_reconnect:
                # 重连：原地更新已注册实例的 client，使其指向新 session
                existing = self._registry._instances.get(tool.name)
                if existing is not None:
                    existing._client = client
                    registered += 1
                    continue
            try:
                self._registry.register_instance(tool.name, tool)
                registered += 1
            except ValueError:
                log.debug("MCP tool '%s' 已注册，跳过", tool.name)
        log.info(
            "MCP server '%s' 已注册/更新 %d 个工具",
            name,
            registered,
        )

    def _schedule_reconnect(self, name: str) -> None:
        if name in self._reconnect_tasks and not self._reconnect_tasks[name].done():
            return
        task = asyncio.create_task(
            self._reconnect_loop(name), name=f"mcp-reconnect-{name}"
        )
        self._reconnect_tasks[name] = task

    async def _reconnect_loop(self, name: str) -> None:
        backoff = _BASE_BACKOFF
        cfg = self._configs.get(name, {})
        while True:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)
            log.info("MCP server '%s' 重连中…", name)
            old = self._clients.pop(name, None)
            if old is not None:
                await old.aclose()
            client = self._make_client(name, cfg)
            try:
                await client.connect()
            except Exception as exc:
                log.warning("MCP server '%s' 重连失败: %s，%gs 后再试", name, exc, backoff)
                continue
            self._clients[name] = client
            await self._register_tools(name, client, cfg, is_reconnect=True)
            log.info("MCP server '%s' 重连成功", name)
            return

    async def aclose(self) -> None:
        for task in self._reconnect_tasks.values():
            task.cancel()
        if self._reconnect_tasks:
            await asyncio.gather(*self._reconnect_tasks.values(), return_exceptions=True)
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
        log.debug("MCPManager 已关闭")
