"""单题 runner — 灌 haystack → 定位 gold facts → recall_with_trace → 计算 hit 向量。"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .config import RETRIEVAL_KS, RRF_KS, MMR_KS
from .ingest import ingest_haystack

log = logging.getLogger("mmsg.eval.longmemeval.runner")


async def run_one_question(
    sample: dict[str, Any],
    llm: Any,
    base_temp_dir: Path,
    embedding_provider: Any = None,
) -> dict:
    """对单题完整跑一遍，返回含 hit 向量的原始结果。

    返回结构：
    {
        question_id, question_type, is_abstention, question, answer,
        gold_fact_count,
        discriminator: {need_recall, query},
        discriminator_gold: bool,
        retrieval_hits: [0/1, ...],   # len = 实际候选数
        rrf_hits: [0/1, ...],
        mmr_hits: [0/1, ...],
        elapsed_ms: int,
        error: str | None,
    }
    """
    qid = sample["question_id"]
    qt = sample["question_type"]
    is_abstention = sample["is_abstention"]

    pid = qid[:8]
    workspace = base_temp_dir / pid
    workspace.mkdir(parents=True, exist_ok=True)
    memory_dir = workspace / "memory"

    log.info("[%s] 开始 type=%s abstention=%s", pid, qt, is_abstention)
    t0 = time.perf_counter()

    try:
        memory, recaller, session_message_map = await ingest_haystack(
            sessions=sample["haystack_sessions"],
            memory_dir=memory_dir,
            llm=llm,
            embedding_provider=embedding_provider,
        )

        if recaller is None:
            return _error_result(sample, "no embedding provider / recaller", t0)

        vector_store = memory.vector_store
        gold_fact_ids = _build_gold_fact_ids(
            answer_session_ids=sample["answer_session_ids"],
            session_message_map=session_message_map,
            vector_store=vector_store,
        )
        log.info("[%s] gold facts=%d", pid, len(gold_fact_ids))

        trace = await recaller.recall_with_trace(sample["question"])

        retrieval_hits = [
            1 if (f.id in gold_fact_ids) else 0
            for f in trace.retrieval_candidates
        ]
        rrf_hits = [
            1 if (f.id in gold_fact_ids) else 0
            for f in trace.rrf_ranked
        ]
        mmr_hits = [
            1 if (f.id in gold_fact_ids) else 0
            for f in trace.mmr_output
        ]

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        log.info(
            "[%s] 完成 disc=%s ret=%d rrf=%d mmr=%d gold=%d elapsed=%dms",
            pid,
            trace.discriminator.get("need_recall"),
            sum(retrieval_hits), sum(rrf_hits), sum(mmr_hits),
            len(gold_fact_ids), elapsed_ms,
        )

        return {
            "question_id": qid,
            "question_type": qt,
            "is_abstention": is_abstention,
            "question": sample["question"],
            "answer": sample["answer"],
            "gold_fact_count": len(gold_fact_ids),
            "total_fact_count": len(vector_store.all_fact_ids()) if vector_store else 0,
            "discriminator": {
                "need_recall": trace.discriminator.get("need_recall", False),
                "query": trace.discriminator.get("query"),
            },
            "discriminator_gold": not is_abstention,
            "retrieval_hits": retrieval_hits,
            "rrf_hits": rrf_hits,
            "mmr_hits": mmr_hits,
            "elapsed_ms": elapsed_ms,
            "error": None,
        }

    except Exception as exc:
        log.exception("[%s] 异常: %s", pid, exc)
        return _error_result(sample, str(exc), t0)


def _build_gold_fact_ids(
    answer_session_ids: list[str],
    session_message_map: dict[str, list[int]],
    vector_store: Any,
) -> set[int]:
    """gold facts = source_message_ids 与 evidence session 的 message_ids 有交集的 facts。"""
    if vector_store is None:
        return set()

    gold_msg_ids: set[int] = set()
    for sid in answer_session_ids:
        gold_msg_ids.update(session_message_map.get(sid, []))

    if not gold_msg_ids:
        return set()

    gold_fact_ids: set[int] = set()
    for fid in vector_store.all_fact_ids():
        fact = vector_store.get_fact(fid)
        if fact and any(mid in gold_msg_ids for mid in fact.source_message_ids):
            gold_fact_ids.add(fid)

    return gold_fact_ids


def _error_result(sample: dict, error: str, t0: float) -> dict:
    return {
        "question_id": sample["question_id"],
        "question_type": sample["question_type"],
        "is_abstention": sample["is_abstention"],
        "question": sample["question"],
        "answer": sample["answer"],
        "gold_fact_count": 0,
        "total_fact_count": 0,
        "discriminator": {"need_recall": None, "query": None},
        "discriminator_gold": not sample["is_abstention"],
        "retrieval_hits": [],
        "rrf_hits": [],
        "mmr_hits": [],
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "error": error,
    }
