"""In-process async event bus. Topic = event.type. Subscribers match by exact name or prefix wildcard 'foo.*' or '*'."""
from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from .tracing import current_trace_id

log = logging.getLogger("mmsg.bus")

Handler = Callable[["Event"], Awaitable[None]]


class Event(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    trace_id: str | None = None
    ts: float = Field(default_factory=time.time)
    type: str
    source: str
    payload: dict[str, Any] = Field(default_factory=dict)


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
            trace_id=current_trace_id(),
        )
        # snapshot to allow handlers to (un)subscribe during dispatch
        targets = [h for pat, h in self._subs if fnmatch.fnmatchcase(type, pat)]
        if not targets:
            return evt
        results = await asyncio.gather(
            *(self._safe(h, evt) for h in targets), return_exceptions=True
        )
        for r in results:
            if isinstance(r, Exception):
                log.exception("subscriber raised: %s", r)
        return evt

    @staticmethod
    async def _safe(handler: Handler, evt: Event) -> None:
        await handler(evt)
