"""OpenAI-compatible provider. Works with OpenAI, DeepSeek, vLLM, Ollama /v1, etc."""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import llm as _cfg
from .base import ChatMessage, LLMProvider, LLMResponse, StreamChunk, ToolCall


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.model = model or _cfg("model") or self._missing("llm.model")
        self.api_key = api_key or _cfg("api_key") or self._missing("llm.api_key")
        self.base_url = (base_url or _cfg("base_url") or self._missing("llm.base_url")).rstrip("/")
        self.timeout = timeout

    @staticmethod
    def _missing(name: str):
        raise RuntimeError(f"配置缺失: {name} 请在 config.toml 中设置")

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
        if m.content:
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
        # assistant 消息必须包含 content 或 tool_calls 之一
        if m.role == "assistant" and "content" not in d and "tool_calls" not in d:
            d["content"] = ""
        return d

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        body: dict[str, Any] = {
            "model": kwargs.pop("model", self.model),
            "messages": [self._dump_msg(m) for m in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = kwargs.pop("tool_choice", "auto")
        body.update(kwargs)

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        _log = logging.getLogger("mmsg.llm")
        _log.debug("LLM request body: %s", body)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions", json=body, headers=headers
            ) as resp:
                if resp.status_code != 200:
                    _log.error("LLM HTTP %s: %s", resp.status_code, await resp.aread())
                resp.raise_for_status()
                # idx -> {"id": str, "name": str, "arguments": str}
                acc: dict[int, dict[str, str]] = {}
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices") or []
                    if not choices:
                        # usage-only chunk（stream_options include_usage）
                        if chunk.get("usage"):
                            yield StreamChunk(usage=chunk["usage"])
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}

                    text = delta.get("content", "")
                    finish = choice.get("finish_reason")

                    # 流式 tool_calls：逐 chunk 累积 id/name/arguments
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        fn = tc.get("function") or {}
                        if idx not in acc:
                            acc[idx] = {"id": tc.get("id", ""), "name": fn.get("name", ""), "arguments": ""}
                        else:
                            if tc.get("id"):
                                acc[idx]["id"] = tc["id"]
                            if fn.get("name"):
                                acc[idx]["name"] = fn["name"]
                        if fn.get("arguments"):
                            acc[idx]["arguments"] += fn["arguments"]

                    # finish chunk 时，从累积数据构建完整 tool_calls
                    tcs: list[ToolCall] = []
                    if finish and acc:
                        for i in sorted(acc):
                            entry = acc[i]
                            args_str = entry["arguments"]
                            try:
                                args = json.loads(args_str) if args_str else {}
                            except json.JSONDecodeError:
                                args = {}
                            tcs.append(ToolCall(id=entry["id"], name=entry["name"], arguments=args))

                    yield StreamChunk(
                        text=text or None,
                        tool_calls=tcs,
                        finish_reason=finish,
                        usage=chunk.get("usage") or {},
                        done=finish is not None,
                    )
