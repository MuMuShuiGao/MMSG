"""Console sink: subscribes to every bus event and pretty-prints it. Anti-blackbox."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from ..core.bus import Event, EventBus

log = logging.getLogger("mmsg.obs")

_COLORS = {
    "user.input":   "\033[36m",   # cyan
    "llm.request":  "\033[34m",   # blue
    "llm.response": "\033[32m",   # green
    "llm.error":    "\033[31m",   # red
    "tool.call":    "\033[35m",   # magenta
    "tool.result":  "\033[33m",   # yellow
    "tool.error":   "\033[31m",
    "agent.final":  "\033[1;32m",
    "loop.step":    "\033[90m",
    "loop.end":     "\033[90m",
}
_RESET = "\033[0m"


def attach_console_sink(bus: EventBus, *, verbose: bool = False) -> None:
    async def _print(evt: Event) -> None:
        color = _COLORS.get(evt.type, "")
        ts = datetime.fromtimestamp(evt.ts).strftime("%H:%M:%S.%f")[:-3]
        head = f"{color}[{ts} {evt.trace_id or '-'} {evt.type:<14}]{_RESET} src={evt.source}"
        body = _summary(evt) if not verbose else json.dumps(evt.payload, ensure_ascii=False)
        print(f"{head} {body}")
    bus.subscribe("*", _print)


def _summary(evt: Event) -> str:
    p = evt.payload
    t = evt.type
    if t == "user.input":
        return f"text={p.get('text')!r}"
    if t == "llm.request":
        return f"step={p.get('step')} msgs={len(p.get('messages') or [])} tools={p.get('tools')}"
    if t == "llm.response":
        tc = p.get("tool_calls") or []
        return (f"step={p.get('step')} finish={p.get('finish_reason')} "
                f"tool_calls={[c['name'] for c in tc]} content={(p.get('content') or '')[:80]!r}")
    if t == "tool.call":
        return f"{p.get('name')}({p.get('arguments')})"
    if t == "tool.result":
        return f"{p.get('name')} -> {(p.get('result') or '')[:120]!r}"
    if t == "tool.error":
        return f"{p.get('name')} !! {p.get('error')}"
    if t == "agent.final":
        return f"FINAL: {(p.get('text') or '')[:200]!r}"
    if t == "loop.step":
        return f"step {p.get('step')}"
    if t == "loop.end":
        return ""
    return json.dumps(p, ensure_ascii=False)
