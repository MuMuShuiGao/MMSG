"""Embedding provider — 独立于 LLMProvider 的向量化能力。"""
from __future__ import annotations

from typing import Any, Protocol


class EmbeddingProvider(Protocol):
    """与 OpenAI embeddings API 兼容的向量化协议。"""

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        ...


class OpenAIEmbeddingProvider:
    """OpenAI 兼容的 embedding provider（dashscope / openai 均适用）。"""

    name = "openai_embedding"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        timeout: float = 30.0,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        import httpx
        import logging
        log = logging.getLogger(__name__)

        model_name = model or self.model
        headers = {"Authorization": f"Bearer {self.api_key}"}
        all_embeddings: list[list[float]] = []

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for i in range(0, len(texts), 10):
                batch = texts[i : i + 10]
                body = {"model": model_name, "input": batch}
                r = await client.post(
                    f"{self.base_url}/embeddings",
                    json=body,
                    headers=headers,
                )
                if r.is_error:
                    log.error("embedding API error: %s %s", r.status_code, r.text[:500])
                r.raise_for_status()
                data = r.json()
                all_embeddings.extend(item["embedding"] for item in data["data"])

        return all_embeddings


def create_embedding_provider(cfg: Any | None = None) -> OpenAIEmbeddingProvider | None:
    """统一工厂：根据 config 创建 embedding provider，api_key 为默认值则返回 None。"""
    if cfg is None:
        from ..config import embedding as emb_cfg
        cfg = emb_cfg
    api_key = cfg("api_key", "")
    if not api_key or api_key == "sk-your-dashscope-key":
        return None
    return OpenAIEmbeddingProvider(
        model=cfg("model", "text-embedding-v3"),
        api_key=api_key,
        base_url=cfg("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    )
