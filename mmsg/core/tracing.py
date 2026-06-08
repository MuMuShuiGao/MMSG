"""Trace context. trace_id auto-propagates into every published event."""
from __future__ import annotations

import contextvars
import logging
import uuid
from contextlib import contextmanager

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mmsg_trace_id", default=None
)


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def current_trace_id() -> str | None:
    return _trace_id.get()


@contextmanager
def trace_scope(trace_id: str | None = None):
    tid = trace_id or new_trace_id()
    token = _trace_id.set(tid)
    try:
        yield tid
    finally:
        _trace_id.reset(token)


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
