"""标准事件类型名称常量 → 总线保持无脑，payload 结构由生产者用 pydantic 管控。"""

# 用户 / agent 边界
USER_INPUT = "user.input"
AGENT_FINAL = "agent.final"

# llm
LLM_REQUEST = "llm.request"
LLM_RESPONSE = "llm.response"
LLM_ERROR = "llm.error"

# 工具
TOOL_CALL = "tool.call"
TOOL_RESULT = "tool.result"
TOOL_ERROR = "tool.error"

# 记忆
MEMORY_READ = "memory.read"
MEMORY_WRITE = "memory.write"

# 流式
LLM_TOKEN = "llm.token"

# 循环控制
LOOP_STEP = "loop.step"
LOOP_END = "loop.end"

# 交互
USER_CANCEL = "user.cancel"
SESSION_RESET = "session.reset"

# 传输层
TRANSPORT_RAW = "transport.raw"
