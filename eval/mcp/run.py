"""MCP 接入端到端验证 demo。

用法：
  python eval/mcp/run.py

验证路径：
  MCPClient.connect()  →  list_tools()  →  call_tool()  →  MCPTool._extract_text()
  外加 MCPManager 完整注册路径（register_instance → tool_registry.all()）
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 项目根
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

SERVER_SCRIPT = str(Path(__file__).parent / "server.py")


async def test_client_direct() -> None:
    """直接用 MCPClient 测试连接、list_tools、call_tool。"""
    from mmsg.tools.mcp.client import MCPClient
    from mmsg.tools.mcp.adapter import MCPTool

    print("── 1. MCPClient 直连测试 ──")
    client = MCPClient(
        name="demo",
        transport="stdio",
        command=sys.executable,
        args=[SERVER_SCRIPT],
    )
    await client.connect()
    print(f"  connected: {client.connected}")

    tools = await client.list_tools()
    print(f"  工具列表 ({len(tools)} 个):")
    for t in tools:
        print(f"    • {t.name}: {t.description}")

    result = await client.call_tool("echo", {"message": "hello MCP!"})
    text = MCPTool._extract_text(result)
    print(f"  echo('hello MCP!') → {text!r}")
    assert text == "echo: hello MCP!", f"意外结果: {text!r}"

    result2 = await client.call_tool("add", {"a": 3, "b": 4})
    text2 = MCPTool._extract_text(result2)
    print(f"  add(3, 4) → {text2!r}")
    assert text2 == "7.0", f"意外结果: {text2!r}"

    await client.aclose()
    print("  aclose OK\n")


async def test_manager_register() -> None:
    """通过 MCPManager 完整注册路径测试。"""
    from mmsg.tools.mcp.manager import MCPManager
    from mmsg.core.plugin import ToolRegistry

    print("── 2. MCPManager 注册测试 ──")

    registry = ToolRegistry()

    import mmsg.config as cfg_mod
    original_fn = cfg_mod.mcp_servers
    try:
        cfg_mod.mcp_servers = lambda: {
            "demo": {
                "command": sys.executable,
                "args": [SERVER_SCRIPT],
                "risk": "safe",
                "timeout": 10,
            }
        }

        manager = MCPManager(registry)
        await manager.start()

        all_tools = registry.all()
        mcp_tools = {k: v for k, v in all_tools.items() if k.startswith("mcp__demo__")}
        print(f"  注册工具: {list(mcp_tools)}")
        assert len(mcp_tools) == 2, f"期望 2 个工具，实际: {list(mcp_tools)}"

        echo_tool = mcp_tools["mcp__demo__echo"]
        print(f"  echo tool risk: {echo_tool.risk}")
        assert echo_tool.risk == "safe"

        result = await echo_tool.run(message="via manager!")
        print(f"  echo('via manager!') → {result!r}")
        assert result == "echo: via manager!"

        await manager.aclose()
        print("  aclose OK\n")
    finally:
        cfg_mod.mcp_servers = original_fn


async def main() -> None:
    try:
        await test_client_direct()
        await test_manager_register()
        print("✓ 全部测试通过")
    except AssertionError as e:
        print(f"\n✗ 断言失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ 异常: {type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
