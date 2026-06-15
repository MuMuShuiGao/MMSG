"""Fact 数据模型 — 向量库召回的最小单元。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class Fact(BaseModel):
    id: int | None = None
    content: str                                                    # LLM 提取的事实陈述（含专有名词原文）
    source_message_ids: list[int] = Field(default_factory=list)     # 哪些 message 提取出的
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    mention_count: int = 1                                          # 合并 worker 累计
    last_mentioned_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    embedding: list[float] | None = None                            # 1024 维，仅召回时填充
    rrf_score: float | None = None                                  # 召回时填充
    distance: float | None = None                                   # 召回时填充
    bm25_rank: float | None = None                                   # sparse 排名，仅检索时填充

    def to_row(self) -> dict[str, Any]:
        import json
        return {
            "content": self.content,
            "source_message_ids": json.dumps(self.source_message_ids, ensure_ascii=False),
            "created_at": self.created_at,
            "mention_count": self.mention_count,
            "last_mentioned_at": self.last_mentioned_at,
        }
