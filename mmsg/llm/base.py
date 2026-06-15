"""LLM provider abstraction. All providers are plugins.

ChatMessage = OpenAI-style {role, content, [tool_calls], [tool_call_id], [name]}.
Tool schema = OpenAI function calling format. Keeps interop wide.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None  # for role=tool
    name: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    message: ChatMessage
    finish_reason: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class StreamChunk(BaseModel):
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    done: bool = False


class LLMProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse: ...

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """Default: call chat() and yield single chunk. Override for real streaming."""
        resp = await self.chat(messages, tools=tools, **kwargs)
        yield StreamChunk(
            text=resp.message.content or "",
            tool_calls=resp.message.tool_calls,
            finish_reason=resp.finish_reason,
            usage=resp.usage,
            done=True,
        )
