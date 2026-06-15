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
        dimensions: int,
        timeout: float = 30.0,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.dimensions = dimensions
        self.timeout = timeout

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        import httpx

        body = {
            "model": model or self.model,
            "input": texts,
            "dimensions": self.dimensions,
            "encoding_format": "float",
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                f"{self.base_url}/embeddings",
                json=body,
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
        return [item["embedding"] for item in data["data"]]


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
        dimensions=int(cfg("dimensions", 1024)),
    )
