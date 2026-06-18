"""LongMemEval 评测配置。"""
from __future__ import annotations

from dataclasses import dataclass, field

# 跳过 temporal-reasoning（需要 timestamp 注入，TODO）
SKIP_QUESTION_TYPES: set[str] = {"temporal_reasoning"}

# 视为"不需要召回"的 question_type（gold = no recall）
ABSTENTION_QUESTION_TYPE = "no_answer_abstain"

# 各 stage 评测的 k 值
RETRIEVAL_KS: list[int] = [10, 20, 30, 60]
RRF_KS: list[int] = [5, 10, 20]
MMR_KS: list[int] = [1, 3, 5]


@dataclass(frozen=True)
class LongMemEvalConfig:
    data_path: str = ""
    n: int = 10                         # stratified 抽样题数
    seed: int = 42
    concurrency: int = 1
    variant: str = "oracle"             # oracle / s / m（仅文档用，实际由 data_path 决定）
