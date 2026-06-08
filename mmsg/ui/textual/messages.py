"""Textual 消息类型。解耦 widgets 和 EventBus 数据格式。"""

from textual.message import Message


class UserSubmit(Message):
    """用户回车提交输入。"""
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class AgentStart(Message):
    """新一轮 agent 循环开始。"""
    def __init__(self, step: int) -> None:
        super().__init__()
        self.step = step


class AgentTokenDelta(Message):
    """LLM 流式输出的 token。"""
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ToolCallStart(Message):
    """工具调用发起。"""
    def __init__(self, step: int, tool_id: str, name: str, arguments: dict) -> None:
        super().__init__()
        self.step = step
        self.tool_id = tool_id
        self.name = name
        self.arguments = arguments


class ToolCallResult(Message):
    """工具调用结果。"""
    def __init__(self, tool_id: str, name: str, result: str) -> None:
        super().__init__()
        self.tool_id = tool_id
        self.name = name
        self.result = result


class ToolCallError(Message):
    """工具调用出错。"""
    def __init__(self, tool_id: str, name: str, error: str) -> None:
        super().__init__()
        self.tool_id = tool_id
        self.name = name
        self.error = error


class AgentFinal(Message):
    """Agent 本轮回答完成。"""
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class StatusChange(Message):
    """更新底部状态栏。"""
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ClearScreen(Message):
    """清空聊天记录。"""
    pass
