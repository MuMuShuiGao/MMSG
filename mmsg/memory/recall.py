"""召回协调器：判别器 LLM → hybrid 检索 → RRF + MMR → top-k facts。

每个 turn 由 Reasoner 调用一次 recall_for_turn()，多 step 共享结果。
"""
from __future__ import annotations

import logging
from typing import Any

import jieba

from mmsg.common import parse_json

from .fact import Fact
from .protocol import MemoryRuntime
from ..llm.base import ChatMessage, LLMProvider
from .engines.default.vector_store import VectorStore

log = logging.getLogger("mmsg.memory.recall")

_DISCRIMINATOR_PROMPT = """判断用户最新消息是否需要从历史记忆中检索内容。
- 闲聊、确认、简短指令（如"嗯""好""继续""知道了"）→ 不需要
- 提到"之前""上次""我说过""记得""你之前说"等回顾性表达 → 需要
- 涉及具体事实、偏好、历史状态、个人信息、项目细节的提问 → 需要
- 如果用户问的问题是 AI 能从当前上下文直接回答的 → 不需要

如果需要，将用户消息改写为最适合检索的 query（保留专有名词原文、版本号、人名、项目名）。

输出 JSON，只输出 JSON，不要其他文字：
{"need_recall": false}
或
{"need_recall": true, "query": "改写后的检索 query"}"""


class Recaller:
    """召回协调器。

    用法：
        recaller = Recaller(memory, llm, embedding_provider)
        facts = await recaller.recall_for_turn(user_msg)
        # facts 是 top-5 经过 MMR 去重的事实
    """

    def __init__(
        self,
        memory: MemoryRuntime,
        llm: LLMProvider,
        embedding_provider: Any = None,  # EmbeddingProvider
        dense_k: int = 30,
        sparse_k: int = 30,
        mmr_lambda: float = 0.7,
        output_k: int = 5,
        rrf_k: int = 60,
    ) -> None:
        self._memory = memory
        self._llm = llm
        self._embed = embedding_provider
        self._dense_k = dense_k
        self._sparse_k = sparse_k
        self._mmr_lambda = mmr_lambda
        self._output_k = output_k
        self._rrf_k = rrf_k
        self._store: VectorStore | None = memory.vector_store

    async def recall_for_turn(self, user_msg: str) -> list[Fact]:
        if not self._store or not self._embed:
            return []

        decision = await self._classify(user_msg)
        if not decision.get("need_recall"):
            return []

        query = decision.get("query", user_msg)
        return await self._hybrid_recall(query)

    async def _classify(self, user_msg: str) -> dict:
        try:
            resp = await self._llm.chat(
                messages=[
                    ChatMessage(role="system", content=_DISCRIMINATOR_PROMPT),
                    ChatMessage(role="user", content=f"用户消息：{user_msg}"),
                ],
            )
            raw = resp.message.content or ""
            data = parse_json(raw)
            if data and isinstance(data, dict):
                return data
        except Exception:
            log.exception("判别器 LLM 调用失败，降级为不召回")
        return {"need_recall": False}

    async def _hybrid_recall(self, query: str) -> list[Fact]:
        try:
            query_vecs = await self._embed.embed([query])
            query_vec = query_vecs[0]
        except Exception:
            log.exception("Embedding 调用失败，降级为空结果")
            return []

        tokens = " ".join(jieba.cut(query))

        candidates = self._store.hybrid_search(
            embedding=query_vec,
            tokens=tokens,
            dense_k=self._dense_k,
            sparse_k=self._sparse_k,
        )

        if not candidates:
            return []

        # RRF 融合
        scored = _rrf_fusion(candidates, k=self._rrf_k)
        # MMR 去重
        return _mmr(scored, self._store, self._output_k, self._mmr_lambda)


def _rrf_fusion(candidates: list[Fact], k: int = 60) -> list[Fact]:
    """Reciprocal Rank Fusion，distance 越小越好，bm25_rank 越小越好。"""
    dense = [f for f in candidates if f.distance is not None]
    sparse = [f for f in candidates if f.bm25_rank is not None]

    dense.sort(key=lambda f: f.distance)
    for rank, f in enumerate(dense):
        f.rrf_score = (f.rrf_score or 0.0) + 1.0 / (k + rank + 1)

    sparse.sort(key=lambda f: f.bm25_rank)
    for rank, f in enumerate(sparse):
        f.rrf_score = (f.rrf_score or 0.0) + 1.0 / (k + rank + 1)

    fused = dense + [f for f in sparse if f not in dense]
    fused.sort(key=lambda f: -(f.rrf_score or 0.0))
    return fused[:20]


def _mmr(candidates: list[Fact], store: VectorStore, k: int, lambda_: float) -> list[Fact]:
    from math import inf

    selected: list[Fact] = []
    remaining = list(candidates)

    while len(selected) < k and remaining:
        if not selected:
            selected.append(remaining.pop(0))
            continue

        best, best_score = None, -inf
        for f in remaining:
            rel = f.rrf_score or 0.0
            sim = _max_cos_to_selected(f, selected, store)
            score = lambda_ * rel - (1.0 - lambda_) * sim
            if score > best_score:
                best, best_score = f, score

        if best:
            selected.append(best)
            remaining.remove(best)

    return selected


def _max_cos_to_selected(fact: Fact, selected: list[Fact], store: VectorStore) -> float:
    vec = store.get_fact_embedding(fact.id) if fact.id else None
    if not vec:
        return 0.0
    max_sim = 0.0
    for s in selected:
        sv = store.get_fact_embedding(s.id) if s.id else None
        if not sv:
            continue
        sim = _cosine_similarity(vec, sv)
        if sim > max_sim:
            max_sim = sim
    return max_sim


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
