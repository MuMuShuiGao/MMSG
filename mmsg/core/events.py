"""Canonical event type names. String constants → keep bus dumb, payloads typed via pydantic at the producer."""

# user / agent boundary
USER_INPUT = "user.input"
AGENT_FINAL = "agent.final"

# llm
LLM_REQUEST = "llm.request"
LLM_RESPONSE = "llm.response"
LLM_ERROR = "llm.error"

# tool
TOOL_CALL = "tool.call"
TOOL_RESULT = "tool.result"
TOOL_ERROR = "tool.error"

# memory
MEMORY_READ = "memory.read"
MEMORY_WRITE = "memory.write"

# loop control
LOOP_STEP = "loop.step"
LOOP_END = "loop.end"
