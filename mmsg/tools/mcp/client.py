"""MCPClient: 单个 MCP server 的连接封装。

支持 stdio（子进程）和 streamable-http 两种 transport。
公开 list_tools() / call_tool()；连接断开时抛 MCPConnectionError。
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

log = logging.getLogger("mmsg.mcp.client")


class MCPConnectionError(RuntimeError):
    """MCP server 连接失败或会话中断。"""


class MCPClient:
    """单 server MCP 客户端，持有 ClientSession 生命周期。"""

    def __init__(
        self,
        *,
        name: str,
        transport: str = "stdio",
        # stdio 参数
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        # http 参数
        url: str | None = None,
        headers: dict[str, str] | None = None,
        # 通用
        connect_timeout: float = 60.0,
    ) -> None:
        self.name = name
        self._transport = transport
        self._command = command
        self._args = args or []
        self._env = env
        self._cwd = cwd
        self._url = url
        self._headers = headers or {}
        self._connect_timeout = connect_timeout

        self._session: Any = None
        self._exit_stack: Any = None

    async def connect(self) -> None:
        """初始化连接，完成 MCP initialize 握手。"""
        try:
            from mcp import ClientSession
        except ImportError as e:
            raise MCPConnectionError(
                "mcp SDK 未安装。请运行: pip install 'mmsg[mcp]'"
            ) from e

        stack = AsyncExitStack()
        try:
            if self._transport == "stdio":
                await self._connect_stdio(stack, ClientSession)
            elif self._transport in ("http", "streamable-http"):
                await self._connect_http(stack, ClientSession)
            else:
                raise MCPConnectionError(f"不支持的 transport: {self._transport!r}")
        except Exception:
            await stack.aclose()
            raise
        self._exit_stack = stack

    async def _connect_stdio(self, stack, ClientSession) -> None:
        from mcp.client.stdio import StdioServerParameters, stdio_client

        if not self._command:
            raise MCPConnectionError(f"server '{self.name}' stdio 模式缺少 command")

        merged_env = {**os.environ}
        if self._env:
            merged_env.update(self._env)

        params = StdioServerParameters(
            command=self._command,
            args=self._args,
            env=merged_env,
            cwd=str(self._cwd) if self._cwd is not None else None,
        )

        read, write = await asyncio.wait_for(
            stack.enter_async_context(stdio_client(params)),
            timeout=self._connect_timeout,
        )
        session: ClientSession = await stack.enter_async_context(
            ClientSession(read, write)
        )
        await asyncio.wait_for(session.initialize(), timeout=self._connect_timeout)
        self._session = session
        log.info("MCP server '%s' (stdio) 连接成功", self.name)

    async def _connect_http(self, stack, ClientSession) -> None:
        try:
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError:
            from mcp.client.sse import sse_client as streamablehttp_client  # type: ignore[no-redef]

        if not self._url:
            raise MCPConnectionError(f"server '{self.name}' http 模式缺少 url")

        read, write, _ = await asyncio.wait_for(
            stack.enter_async_context(
                streamablehttp_client(self._url, headers=self._headers)
            ),
            timeout=self._connect_timeout,
        )
        session: ClientSession = await stack.enter_async_context(
            ClientSession(read, write)
        )
        await asyncio.wait_for(session.initialize(), timeout=self._connect_timeout)
        self._session = session
        log.info("MCP server '%s' (http) 连接成功", self.name)

    async def list_tools(self) -> list[Any]:
        """返回 mcp.types.Tool 列表。"""
        self._check_connected()
        result = await self._session.list_tools()
        return result.tools

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any], timeout: float = 30.0
    ) -> Any:
        """调用工具，返回 CallToolResult。超时/连接中断抛 MCPConnectionError。"""
        self._check_connected()
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(tool_name, arguments=arguments),
                timeout=timeout,
            )
            return result
        except asyncio.TimeoutError:
            raise MCPConnectionError(
                f"MCP server '{self.name}' 调用 '{tool_name}' 超时 ({timeout}s)"
            )

    async def aclose(self) -> None:
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception:
                log.debug("MCP server '%s' 关闭时异常（忽略）", self.name)
            finally:
                self._exit_stack = None
                self._session = None

    @property
    def connected(self) -> bool:
        return self._session is not None

    def _check_connected(self) -> None:
        if self._session is None:
            raise MCPConnectionError(f"MCP server '{self.name}' 未连接")
