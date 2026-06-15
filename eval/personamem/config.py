"""评测量级预设。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TierConfig:
    name: str
    sample_count: int
    default_concurrency: int


TIERS: dict[str, TierConfig] = {
    "smoke": TierConfig(name="smoke", sample_count=100, default_concurrency=2),
    "standard": TierConfig(name="standard", sample_count=500, default_concurrency=2),
    "full": TierConfig(name="full", sample_count=2000, default_concurrency=4),
}
