"""进程内异步事件总线。主题 = event.type，订阅者按精确名称、前缀通配符 'foo.*' 或 '*' 匹配。"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

log = logging.getLogger("mmsg.bus")

Handler = Callable[["Event"], Awaitable[None]]


class Event(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: float = Field(default_factory=time.time)
    type: str
    source: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def from_json(data: str) -> "Event":
        """从 JSON 字符串反序列化 Event，用于传输层。"""
        raw = json.loads(data)
        return Event(**raw)


class EventBus:
    def __init__(self) -> None:
        self._subs: list[tuple[str, Handler]] = []
        self._lock = asyncio.Lock()

    def subscribe(self, pattern: str, handler: Handler) -> Callable[[], None]:
        entry = (pattern, handler)
        self._subs.append(entry)

        def _unsub() -> None:
            try:
                self._subs.remove(entry)
            except ValueError:
                pass

        return _unsub

    async def publish(
        self,
        type: str,
        source: str,
        payload: dict[str, Any] | None = None,
    ) -> Event:
        evt = Event(
            type=type,
            source=source,
            payload=payload or {},
        )
        # 快照副本，允许 handler 在分发过程中 (取消) 订阅
        targets = [h for pat, h in self._subs if fnmatch.fnmatchcase(type, pat)]
        if not targets:
            return evt
        results = await asyncio.gather(
            *(self._safe(h, evt) for h in targets), return_exceptions=True
        )
        for r in results:
            if isinstance(r, Exception):
                log.exception("订阅者异常: %s", r)
        return evt

    @staticmethod
    async def _safe(handler: Handler, evt: Event) -> None:
        await handler(evt)
