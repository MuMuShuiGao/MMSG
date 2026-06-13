"""Channel adapters: external IM platforms ↔ MessageBus."""

__all__ = ["QQBotChannel", "FeishuChannel"]


def __getattr__(name: str):
    if name == "QQBotChannel":
        from .qqbot import QQBotChannel
        return QQBotChannel
    if name == "FeishuChannel":
        from .feishu import FeishuChannel
        return FeishuChannel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
