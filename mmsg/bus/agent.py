"""Agent 内部总线事件常量。只在 AgentLoop 内部使用。

Turn ─────────────────────────────────────────────
│
├─ BeforeTurn               (Observer,1 次)
│
├─ for step in 0..max:
│   ├─ BeforeStep           (Interceptor: 改 messages/tools)
│   ├─ LLM 调用
│   ├─ AfterReasoning       (Interceptor: 改 content/tool_calls)
│   ├─ for tc in tool_calls:
│   │     ├─ BeforeToolCall (Observer)
│   │     ├─ tool.run()
│   │     └─ AfterToolCall  (Observer)
│   └─ AfterStep            (Observer: metrics)
│
└─ AfterTurn                (Observer,1 次)
"""

from enum import StrEnum

from .eventbus import EventBus


class AgentEvent(StrEnum):
    # Interceptor — 顺序执行,handler 返回新 Event 改写管道
    BeforeStep = "before_step"
    AfterReasoning = "after_reasoning"

    # Observer — 并行通知,纯观察
    BeforeTurn = "before_turn"
    BeforeToolCall = "before_tool_call"
    AfterToolCall = "after_tool_call"
    AfterStep = "after_step"
    AfterTurn = "after_turn"


class AgentBus(EventBus):
    """Agent 内部专用事件总线。"""
