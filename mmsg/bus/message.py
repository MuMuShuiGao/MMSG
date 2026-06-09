"""外部消息总线 + 事件常量。channel / TUI / transport 只碰这条。"""

from .eventbus import EventBus

MESSAGE_INBOUND = "message.inbound"
MESSAGE_OUTBOUND = "message.outbound"

SESSION_RESET = "session.reset"
USER_CANCEL = "user.cancel"

TRANSPORT_RAW = "transport.raw"


class MessageBus(EventBus):
    """外部消息总线。channel / TUI / transport 从此进出。"""
