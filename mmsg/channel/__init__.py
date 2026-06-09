"""Channel adapters: external IM platforms ↔ MessageBus."""

__all__ = ["QQBotChannel"]


def __getattr__(name: str):
    if name == "QQBotChannel":
        from .qqbot import QQBotChannel
        return QQBotChannel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
