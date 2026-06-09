"""Console sink: subscribes to every bus event and pretty-prints it. Anti-blackbox."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from ..bus.agent import AgentBus
from ..bus.agent import (
    LLM_REQUEST, LLM_RESPONSE, LLM_TOKEN, LLM_ERROR,
    TOOL_CALL, TOOL_RESULT, TOOL_ERROR,
    AGENT_FINAL, LOOP_STEP, LOOP_END,
)
from ..bus.eventbus import Event

log = logging.getLogger("mmsg.obs")

_COLORS = {
    LLM_REQUEST:  "\033[34m",
    LLM_RESPONSE: "\033[32m",
    LLM_TOKEN:    "\033[33m",
    LLM_ERROR:    "\033[31m",
    TOOL_CALL:    "\033[35m",
    TOOL_RESULT:  "\033[33m",
    TOOL_ERROR:   "\033[31m",
    AGENT_FINAL:  "\033[1;32m",
    LOOP_STEP:    "\033[90m",
    LOOP_END:     "\033[90m",
}
_RESET = "\033[0m"


def attach_console_sink(bus: AgentBus, *, verbose: bool = False) -> None:
    async def _print(evt: Event) -> None:
        color = _COLORS.get(evt.type, "")
        ts = datetime.fromtimestamp(evt.ts).strftime("%H:%M:%S.%f")[:-3]
        head = f"{color}[{ts} {evt.type:<14}]{_RESET} src={evt.source}"
        body = _summary(evt) if not verbose else json.dumps(evt.payload, ensure_ascii=False)
        print(f"{head} {body}")
    bus.subscribe("*", _print)


def _summary(evt: Event) -> str:
    p = evt.payload
    t = evt.type
    if t == LLM_REQUEST:
        return f"step={p.get('step')} msgs={len(p.get('messages') or [])} tools={p.get('tools')}"
    if t == LLM_RESPONSE:
        tc = p.get("tool_calls") or []
        return (f"step={p.get('step')} finish={p.get('finish_reason')} "
                f"tool_calls={[c['name'] for c in tc]} content={(p.get('content') or '')[:80]!r}")
    if t == TOOL_CALL:
        return f"{p.get('name')}({p.get('arguments')})"
    if t == TOOL_RESULT:
        return f"{p.get('name')} -> {(p.get('result') or '')[:120]!r}"
    if t == TOOL_ERROR:
        return f"{p.get('name')} !! {p.get('error')}"
    if t == AGENT_FINAL:
        return f"FINAL: {(p.get('text') or '')[:200]!r}"
    if t == LOOP_STEP:
        return f"step {p.get('step')}"
    if t == LOOP_END:
        return ""
    return json.dumps(p, ensure_ascii=False)
