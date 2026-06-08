"""OpenAI-compatible provider. Works with OpenAI, DeepSeek, vLLM, Ollama /v1, etc."""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .base import ChatMessage, LLMProvider, LLMResponse, ToolCall


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": kwargs.pop("model", self.model),
            "messages": [self._dump_msg(m) for m in messages],
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = kwargs.pop("tool_choice", "auto")
        body.update(kwargs)

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                f"{self.base_url}/chat/completions", json=body, headers=headers
            )
            r.raise_for_status()
            data = r.json()

        choice = data["choices"][0]
        msg = choice["message"]
        tool_calls = [
            ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=json.loads(tc["function"].get("arguments") or "{}"),
            )
            for tc in msg.get("tool_calls") or []
        ]
        return LLMResponse(
            message=ChatMessage(
                role="assistant",
                content=msg.get("content"),
                tool_calls=tool_calls,
            ),
            finish_reason=choice.get("finish_reason"),
            usage=data.get("usage") or {},
            raw=data,
        )

    @staticmethod
    def _dump_msg(m: ChatMessage) -> dict[str, Any]:
        d: dict[str, Any] = {"role": m.role}
        if m.content is not None:
            d["content"] = m.content
        if m.name:
            d["name"] = m.name
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in m.tool_calls
            ]
        return d
