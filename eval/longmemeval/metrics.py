"""召回 Metric 计算 — Recall@k, MRR, nDCG, 判别器 F1, abstention FP rate。

所有指标按 question_type 分组，外加 overall。
"""
from __future__ import annotations

from math import log2

from .config import RETRIEVAL_KS, RRF_KS, MMR_KS, ABSTENTION_QUESTION_TYPE


# ── 基础公式 ─────────────────────────────────────────────────────

def recall_at_k(hits: list[int], gold_count: int, k: int) -> float:
    if gold_count == 0:
        return 0.0
    return sum(hits[:k]) / gold_count


def precision_at_k(hits: list[int], k: int) -> float:
    if k == 0:
        return 0.0
    return sum(hits[:k]) / k


def mrr(hits: list[int]) -> float:
    for i, h in enumerate(hits):
        if h:
            return 1.0 / (i + 1)
    return 0.0


def dcg_at_k(hits: list[int], k: int) -> float:
    return sum(h / log2(i + 2) for i, h in enumerate(hits[:k]))


def ndcg_at_k(hits: list[int], gold_count: int, k: int) -> float:
    ideal = [1] * min(gold_count, k) + [0] * max(0, k - gold_count)
    idcg = dcg_at_k(ideal, k)
    return dcg_at_k(hits, k) / idcg if idcg > 0 else 0.0


# ── 聚合 ────────────────────────────────────────────────────────

def compute_metrics(results: list[dict]) -> dict:
    """聚合所有指标，按 question_type 分组 + overall。

    results: run_one_question 返回的列表（含 error=None 的有效结果）
    返回 {overall: {...}, by_type: {qt: {...}}}
    """
    valid = [r for r in results if r.get("error") is None]

    groups: dict[str, list[dict]] = {"overall": valid}
    for r in valid:
        qt = r.get("question_type", "unknown")
        groups.setdefault(qt, []).append(r)

    out: dict[str, dict] = {}
    for label, group in sorted(groups.items()):
        out[label] = _compute_group(group)
    return out


def _compute_group(results: list[dict]) -> dict:
    non_abs = [r for r in results if not r["is_abstention"]]
    abs_results = [r for r in results if r["is_abstention"]]

    m: dict = {
        "n_total": len(results),
        "n_non_abstention": len(non_abs),
        "n_abstention": len(abs_results),
    }

    # 判别器 P/R/F1
    m["discriminator"] = _discriminator_metrics(results)

    # Retrieval Recall@k
    m["retrieval"] = {}
    for k in RETRIEVAL_KS:
        vals = [recall_at_k(r["retrieval_hits"], r["gold_fact_count"], k) for r in non_abs]
        m["retrieval"][f"recall@{k}"] = _mean(vals)

    # RRF Recall@k + MRR + nDCG
    m["rrf"] = {}
    for k in RRF_KS:
        vals_r = [recall_at_k(r["rrf_hits"], r["gold_fact_count"], k) for r in non_abs]
        vals_mrr = [mrr(r["rrf_hits"][:k]) for r in non_abs]
        vals_ndcg = [ndcg_at_k(r["rrf_hits"], r["gold_fact_count"], k) for r in non_abs]
        m["rrf"][f"recall@{k}"] = _mean(vals_r)
        m["rrf"][f"mrr@{k}"] = _mean(vals_mrr)
        m["rrf"][f"ndcg@{k}"] = _mean(vals_ndcg)

    # MMR Recall@k + MRR + nDCG + Precision@k
    m["mmr"] = {}
    for k in MMR_KS:
        vals_r = [recall_at_k(r["mmr_hits"], r["gold_fact_count"], k) for r in non_abs]
        vals_p = [precision_at_k(r["mmr_hits"], k) for r in non_abs]
        vals_mrr = [mrr(r["mmr_hits"][:k]) for r in non_abs]
        vals_ndcg = [ndcg_at_k(r["mmr_hits"], r["gold_fact_count"], k) for r in non_abs]
        m["mmr"][f"recall@{k}"] = _mean(vals_r)
        m["mmr"][f"precision@{k}"] = _mean(vals_p)
        m["mmr"][f"mrr@{k}"] = _mean(vals_mrr)
        m["mmr"][f"ndcg@{k}"] = _mean(vals_ndcg)

    # 端到端 realistic（尊重判别器）vs oracle-gated（绕过）
    m["e2e"] = _e2e_metrics(non_abs, k=5)

    # Abstention FP rate（平均每题误召回 fact 数）
    if abs_results:
        fp_counts = [len(r["mmr_hits"]) for r in abs_results]
        m["abstention_fp_rate"] = _mean(fp_counts)
    else:
        m["abstention_fp_rate"] = None

    return m


def _discriminator_metrics(results: list[dict]) -> dict:
    tp = fp = fn = tn = 0
    for r in results:
        gold = r["discriminator_gold"]
        pred = r["discriminator"].get("need_recall")
        if pred is None:
            continue
        if gold and pred:
            tp += 1
        elif not gold and pred:
            fp += 1
        elif gold and not pred:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    }


def _e2e_metrics(non_abs: list[dict], k: int = 5) -> dict:
    """realistic: 判别器 say no → 视为空召回；oracle-gated: 始终用 mmr_hits。"""
    real_vals = []
    oracle_vals = []
    for r in non_abs:
        mmr_hits = r["mmr_hits"]
        oracle_vals.append(recall_at_k(mmr_hits, r["gold_fact_count"], k))
        if r["discriminator"].get("need_recall"):
            real_vals.append(recall_at_k(mmr_hits, r["gold_fact_count"], k))
        else:
            real_vals.append(0.0)
    return {
        f"realistic_recall@{k}": _mean(real_vals),
        f"oracle_gated_recall@{k}": _mean(oracle_vals),
    }


def _mean(vals: list[float]) -> float | None:
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)
