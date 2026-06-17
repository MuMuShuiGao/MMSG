"""最小 MCP server，用于本地测试。

工具：
  echo(message)  — 原样返回消息
  add(a, b)      — 返回两数之和
"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo-server")


@mcp.tool()
def echo(message: str) -> str:
    """原样返回消息。"""
    return f"echo: {message}"


@mcp.tool()
def add(a: float, b: float) -> str:
    """返回 a + b 的结果。"""
    return str(a + b)


if __name__ == "__main__":
    mcp.run()
