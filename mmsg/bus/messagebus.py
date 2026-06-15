"""外部消息总线：inbound/outbound 走生产者-消费者队列，可观测事件走内嵌 EventBus。

channel/TUI  →  publish_inbound()  →  [_inbound queue]  →  consume_inbound()  →  AgentLoop.serve()
AgentLoop    →  publish_outbound()  →  [_outbound queue] →  subscribe_outbound() →  channel 发消息
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .eventbus import EventBus, Event

log = logging.getLogger("mmsg.bus")

# 事件常量 — 仅用于 events bus 上的可观测事件
SESSION_RESET = "session.reset"
TRANSPORT_RAW = "transport.raw"

# 保留给 transport 跨进程 relay 使用
MESSAGE_INBOUND = "message.inbound"


@dataclass
class BusItem:
    """消息总线 item，方向由队列/方法名承载。"""
    source: str
    payload: dict


# 向后兼容别名
OutboundItem = BusItem

OutboundHandler = Callable[["BusItem"], Awaitable[None]]


class MessageBus:
    """消息总线：inbound/outbound 队列 + 可观测事件流。"""

    def __init__(self) -> None:
        self._inbound: asyncio.Queue[BusItem] = asyncio.Queue()
        self._outbound: asyncio.Queue[BusItem] = asyncio.Queue()
        self._outbound_handlers: list[tuple[str, OutboundHandler]] = []
        self._dispatch_task: asyncio.Task | None = None
        self.events = EventBus()

    # ── Inbound（channel → agent）──────────────────────

    async def publish_inbound(self, source: str, payload: dict) -> BusItem:
        """channel/TUI 发布入站消息到队列，非阻塞（除非队列满）。"""
        item = BusItem(source=source, payload=payload)
        await self._inbound.put(item)
        return item

    async def consume_inbound(self) -> BusItem:
        """Agent 阻塞等待下一条入站消息。"""
        return await self._inbound.get()

    # ── Outbound（agent → channel）─────────────────────

    async def publish_outbound(self, source: str, payload: dict) -> None:
        """Agent 发布出站消息到分发队列。"""
        await self._outbound.put(BusItem(source=source, payload=payload))

    def subscribe_outbound(self, pattern: str, handler: OutboundHandler) -> Callable[[], None]:
        """channel 注册出站消息处理器，pattern 匹配 source 字段（fnmatch）。"""
        entry = (pattern, handler)
        self._outbound_handlers.append(entry)
        self._ensure_dispatch()

        def _unsub() -> None:
            try:
                self._outbound_handlers.remove(entry)
            except ValueError:
                pass

        return _unsub

    def _ensure_dispatch(self) -> None:
        if self._dispatch_task is None or self._dispatch_task.done():
            self._dispatch_task = asyncio.create_task(self._dispatch_loop())

    async def _dispatch_loop(self) -> None:
        while True:
            item = await self._outbound.get()
            for pat, handler in self._outbound_handlers:
                if fnmatch.fnmatchcase(item.source, pat):
                    try:
                        await handler(item)
                    except Exception:
                        log.exception("出站处理器异常: %s", pat)
