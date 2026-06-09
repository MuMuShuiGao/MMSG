"""Agent 内部总线 + 事件常量。只在 AgentLoop 内部使用。"""

from .eventbus import EventBus

LLM_REQUEST = "llm.request"
LLM_RESPONSE = "llm.response"
LLM_TOKEN = "llm.token"
LLM_ERROR = "llm.error"

TOOL_CALL = "tool.call"
TOOL_RESULT = "tool.result"
TOOL_ERROR = "tool.error"

MEMORY_READ = "memory.read"
MEMORY_WRITE = "memory.write"

LOOP_STEP = "loop.step"
LOOP_END = "loop.end"

AGENT_FINAL = "agent.final"


class AgentBus(EventBus):
    """Agent 内部专用事件总线，类型标注收口。"""
