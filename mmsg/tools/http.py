"""HTTP 工具：http_get。"""
from __future__ import annotations

import json

from typing import Any, ClassVar

import httpx

from .base import Tool

_MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB
_TIMEOUT = 10.0
_UA = "MMSG-agent/0.1"
_HEADERS = {"User-Agent": _UA}

_JSON_SUBTYPES: frozenset[str] = frozenset({
    "json", "application/json", "application/ld+json",
    "application/vnd.api+json", "application/problem+json",
})


class HttpGetTool(Tool):
    name = "http_get"
    description = (
        "发起 HTTP GET 请求并以文本形式返回响应体。"
        "JSON 响应自动格式化缩进。"
        "自动跟随重定向，最大响应体 1 MB，超时 10 秒。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要请求的完整 URL。"},
        },
        "required": ["url"],
    }
    risk: ClassVar[str] = "network"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True, headers=_HEADERS
        )

    async def run(self, url: str, **_: Any) -> str:
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            body = resp.content
            if len(body) > _MAX_BODY_BYTES:
                body = body[:_MAX_BODY_BYTES]
            text = body.decode("utf-8", errors="replace")
            if self._is_json(resp.headers.get("content-type", "")):
                try:
                    data = json.loads(text)
                    return json.dumps(data, indent=2, ensure_ascii=False)
                except json.JSONDecodeError:
                    pass
            return text
        except httpx.HTTPStatusError as e:
            return f"HTTP {e.response.status_code}: {e.request.url}"
        except Exception as e:
            return f"请求失败: {e}"

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _is_json(content_type: str) -> bool:
        main = content_type.split(";", 1)[0].strip().lower()
        return main in _JSON_SUBTYPES
