"""Console sink: subscribes to every bus event and pretty-prints it. Anti-blackbox."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from ..bus.agent import AgentEvent, AgentBus
from ..bus.eventbus import Event

log = logging.getLogger("mmsg.obs")

_COLORS = {
    AgentEvent.BeforeTurn:       "\033[36m",
    AgentEvent.BeforeStep:       "\033[34m",
    AgentEvent.AfterReasoning:   "\033[32m",
    AgentEvent.BeforeToolCall:   "\033[35m",
    AgentEvent.AfterToolCall:    "\033[33m",
    AgentEvent.AfterStep:        "\033[90m",
    AgentEvent.AfterTurn:        "\033[90m",
}
_RESET = "\033[0m"


def attach_console_sink(bus: AgentBus, *, verbose: bool = False) -> None:
    async def _print(evt: Event) -> None:
        color = _COLORS.get(evt.type, "")
        ts = datetime.fromtimestamp(evt.ts).strftime("%H:%M:%S.%f")[:-3]
        head = f"{color}[{ts} {evt.type:<16}]{_RESET} src={evt.source}"
        body = _summary(evt) if not verbose else json.dumps(evt.payload, ensure_ascii=False)
        print(f"{head} {body}")
    bus.subscribe("*", _print)


def _summary(evt: Event) -> str:
    p = evt.payload
    t = evt.type
    if t == AgentEvent.BeforeTurn:
        return ""
    if t == AgentEvent.BeforeStep:
        return f"step={p.get('step')} msgs={len(p.get('messages') or [])} tools={p.get('tools')}"
    if t == AgentEvent.AfterReasoning:
        tc = p.get("tool_calls") or []
        return (f"step={p.get('step')} finish={p.get('finish_reason')} "
                f"tool_calls={[c['name'] for c in tc]} content={(p.get('content') or '')[:80]!r}")
    if t == AgentEvent.BeforeToolCall:
        return f"{p.get('name')}({p.get('arguments')})"
    if t == AgentEvent.AfterToolCall:
        return f"{p.get('name')} -> {(p.get('result') or '')[:120]!r}"
    if t == AgentEvent.AfterStep:
        return f"step {p.get('step')} final={p.get('final')}"
    if t == AgentEvent.AfterTurn:
        return ""
    return json.dumps(p, ensure_ascii=False)
