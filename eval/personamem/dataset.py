"""数据集加载 — HuggingFace PersonaMem，三档随机采样，seed 固定可复现。

PersonaMem 结构：
- questions_XXk.csv: 每题一行，含 shared_context_id + end_index_in_shared_context
- shared_contexts_XXk.jsonl: 对话历史，按 shared_context_id 索引

加载时自动组合：每题从 jsonl 中取出 context[:end_index] 作为灌入历史。
"""
from __future__ import annotations

import ast
import random
from functools import lru_cache

import jsonlines
from datasets import load_dataset as hf_load
from huggingface_hub import hf_hub_download

HF_DATASET = "bowen-upenn/PersonaMem"
REPO_TYPE = "dataset"

SPLIT_MAP = {
    "32k": "persona_mem-32k",
    "128k": "persona_mem-128k",
    "1M": "persona_mem-1M",
}


@lru_cache(maxsize=1)
def _load_shared_contexts(context_size: str) -> dict[str, list[dict]]:
    """下载并加载 shared_contexts jsonl，按 shared_context_id 索引。

    context_size: "32k" / "128k" / "1M"
    """
    filename = f"shared_contexts_{context_size}.jsonl"
    path = hf_hub_download(
        repo_id=HF_DATASET,
        filename=filename,
        repo_type=REPO_TYPE,
    )
    result: dict[str, list[dict]] = {}
    with jsonlines.open(path) as reader:
        for obj in reader:
            # PersonaMem jsonl: {shared_context_id: [{role, content}, ...]}
            for cid, turns in obj.items():
                result[cid] = turns
    return result


def load_personamem(tier: str, seed: int = 42, context_size: str = "32k") -> list[dict]:
    """加载 PersonaMem 并随机采样指定数量。

    tier: smoke / standard / full
    seed: 随机种子
    context_size: 用哪个上下文长度切片 (32k / 128k / 1M)

    返回: [{
        persona_id, question_id, question_type, topic,
        user_question, correct_answer, all_options,
        history_turns,  # 灌入 memory 的对话历史
    }, ...]
    """
    from .config import TIERS
    cfg = TIERS[tier]

    ds = hf_load(HF_DATASET, "benchmark", split=context_size)
    rows = list(ds)

    # 加载 shared context
    shared = _load_shared_contexts(context_size)

    rng = random.Random(seed)
    rng.shuffle(rows)

    actual = min(cfg.sample_count, len(rows))
    selected = rows[:actual]

    results: list[dict] = []
    for row in selected:
        cid = row["shared_context_id"]
        end_idx = row["end_index_in_shared_context"]

        full_context = shared.get(cid, [])
        history_turns = _extract_turns(full_context[:end_idx]) if end_idx else _extract_turns(full_context)

        # 解析选项（PersonaMem 用了 Python repr 格式，含转义单引号，用 ast.literal_eval）
        all_options_raw = row.get("all_options", "[]")
        if isinstance(all_options_raw, str):
            all_options = ast.literal_eval(all_options_raw)
        else:
            all_options = all_options_raw

        results.append({
            "persona_id": str(row.get("persona_id", "")),
            "question_id": row.get("question_id", ""),
            "question_type": row.get("question_type", "unknown"),
            "topic": row.get("topic", ""),
            "user_question": row.get("user_question_or_message", ""),
            "correct_answer": _normalize_answer(row.get("correct_answer", "")),
            "all_options": all_options,
            "history_turns": history_turns,
        })

    return results


def _extract_turns(context: list[dict]) -> list[dict]:
    """从 PersonaMem dialog API dicts 提取对话轮次。

    保留 system（persona 定义）、user、assistant，过滤 name=None 等纯 metadata。
    """
    turns: list[dict] = []
    for msg in context:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role in ("system", "user", "assistant") and content:
            turns.append({"role": role, "content": content})
    return turns


def _normalize_answer(raw: str) -> str:
    """从 '(a)' 或 'A' 等格式归一化为大写字母。"""
    s = raw.strip().lower()
    for ch in "abcd":
        if ch in s:
            return ch.upper()
    return raw.strip().upper()
