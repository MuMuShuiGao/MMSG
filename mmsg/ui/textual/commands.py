"""聊天界面的斜杠命令。"""

from textual.app import App


def handle_command(text: str, app: App) -> bool:
    """解析并分发斜杠命令。返回 True 表示命令已处理。"""
    if not text.startswith("/"):
        return False

    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/exit" or cmd == "/quit":
        app.exit()
    elif cmd == "/clear":
        # 通过传输层发布 session.reset，由服务端处理 memory 清空
        import asyncio
        app.call_from_thread(lambda: asyncio.ensure_future(
            _publish_reset(app)
        ))
    elif cmd == "/help":
        from .widgets.chat_log import ChatLog
        from rich.text import Text
        from textual.widgets import Static
        t = Text()
        t.append("命令:\n", style="bold")
        t.append("  /help", style="cyan")
        t.append("   显示帮助\n")
        t.append("  /clear", style="cyan")
        t.append("  清空会话\n")
        t.append("  /exit", style="cyan")
        t.append("   退出\n")
        t.append("  /quit", style="cyan")
        t.append("   同 /exit\n")
        log = app.query_one(ChatLog)
        log.mount(Static(t))
    else:
        from .widgets.chat_log import ChatLog
        from rich.text import Text
        from textual.widgets import Static
        log = app.query_one(ChatLog)
        log.mount(Static(Text(f"未知命令: {cmd}", style="red")))
    return True


async def _publish_reset(app: App) -> None:
    """通过传输层发布 session.reset 到服务端。"""
    bus = getattr(app, "_mmsg_bus", None)
    if bus:
        await bus.observe("session.reset", "ui", {})
