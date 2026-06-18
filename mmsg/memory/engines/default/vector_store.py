"""向量库封装：sqlite-vec + FTS5 + jieba。

dense lane: vec_fact 虚表 cos 距离检索
sparse lane: fts_fact bm25 全文检索（jieba 切词 → unicode61 空格拼接）
"""
from __future__ import annotations

import json
import logging
import re
import struct
from datetime import datetime, timezone

import jieba
import sqlite3

from ...fact import Fact
from mmsg.storage.schema import VEC_FACT, FTS_FACT

log = logging.getLogger("mmsg.memory.vector_store")


class VectorStore:
    """封装 sqlite 内部的向量检索与全文检索。

    复用 SqliteStore 的连接，不自行创建。
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    # ── fact CRUD ──────────────────────────────────

    def insert_fact(self, fact: Fact, embedding: list[float] | None = None) -> int:
        """写入 fact 表、vec_fact 向量、fts_fact 全文索引。返回 fact_id。"""
        cols = fact.to_row()
        cur = self._conn.execute(
            """INSERT INTO fact (content, source_message_ids, created_at, mention_count, last_mentioned_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                cols["content"],
                cols["source_message_ids"],
                cols["created_at"],
                cols["mention_count"],
                cols["last_mentioned_at"],
            ),
        )
        fact_id = cur.lastrowid

        if embedding:
            self._conn.execute(
                f"INSERT INTO {VEC_FACT} (fact_id, embedding) VALUES (?, ?)",
                (fact_id, _serialize_embedding(embedding)),
            )

        # BM25: jieba 切词后空格拼接，用 rowid 跟 fact 关联
        tokens = " ".join(jieba.cut(fact.content))
        self._conn.execute(
            f"INSERT INTO {FTS_FACT} (rowid, content) VALUES (?, ?)",
            (fact_id, tokens),
        )

        self._conn.commit()
        return fact_id

    def insert_facts_batch(
        self, facts: list[tuple[Fact, list[float] | None]]
    ) -> list[int]:
        """批量写入 fact + embedding + fts。单事务。"""
        ids: list[int] = []
        self._conn.execute("BEGIN")
        try:
            for fact, embedding in facts:
                cols = fact.to_row()
                cur = self._conn.execute(
                    """INSERT INTO fact (content, source_message_ids, created_at, mention_count, last_mentioned_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        cols["content"],
                        cols["source_message_ids"],
                        cols["created_at"],
                        cols["mention_count"],
                        cols["last_mentioned_at"],
                    ),
                )
                fid = cur.lastrowid
                if embedding:
                    self._conn.execute(
                        f"INSERT INTO {VEC_FACT} (fact_id, embedding) VALUES (?, ?)",
                        (fid, _serialize_embedding(embedding)),
                    )
                tokens = " ".join(jieba.cut(fact.content))
                self._conn.execute(
                    f"INSERT INTO {FTS_FACT} (rowid, content) VALUES (?, ?)",
                    (fid, tokens),
                )
                ids.append(fid)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return ids

    def get_fact(self, fact_id: int) -> Fact | None:
        row = self._conn.execute(
            "SELECT * FROM fact WHERE id = ?", (fact_id,)
        ).fetchone()
        return _row_to_fact(row) if row else None

    # ── hybrid 检索 ────────────────────────────────

    def hybrid_search(
        self,
        embedding: list[float],
        tokens: str,
        dense_k: int = 30,
        sparse_k: int = 30,
    ) -> list[Fact]:
        """一条 SQL 同时拿 dense 和 sparse 结果，返回合并列表（未融合）。"""
        vec_blob = _serialize_embedding(embedding)
        # 过滤 FTS5 不支持的特殊字符（单引号、问号等），防止 syntax error
        safe_tokens = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", tokens).strip()
        if not safe_tokens:
            safe_tokens = "x"  # FTS5 MATCH 不接受空字符串

        rows = self._conn.execute(
            f"""
            WITH dense AS (
                SELECT fact_id AS id, distance
                FROM {VEC_FACT}
                WHERE embedding MATCH ?
                  AND k = ?
            ),
            sparse AS (
                SELECT rowid AS id, bm25(fts_fact) AS rank
                FROM {FTS_FACT}
                WHERE fts_fact MATCH ?
                ORDER BY rank DESC
                LIMIT ?
            )
            SELECT f.id, f.content, f.source_message_ids, f.created_at,
                   f.mention_count, f.last_mentioned_at,
                   d.distance, s.rank
            FROM fact f
            LEFT JOIN dense d ON d.id = f.id
            LEFT JOIN sparse s ON s.id = f.id
            WHERE d.id IS NOT NULL OR s.id IS NOT NULL
            """,
            (vec_blob, dense_k, safe_tokens, sparse_k),
        ).fetchall()

        facts: list[Fact] = []
        for row in rows:
            fact = _row_to_fact(row)
            fact.distance = row["distance"]
            fact.bm25_rank = row["rank"]
            facts.append(fact)
        return facts

    # ── merger 用 ──────────────────────────────────

    def find_near_duplicates(
        self, embedding: list[float], threshold: float = 0.97, limit: int = 10
    ) -> list[tuple[Fact, float]]:
        """查找 cos 相似度 > threshold 的候选 fact。返回 (fact, cos_similarity)。"""
        vec_blob = _serialize_embedding(embedding)
        rows = self._conn.execute(
            f"""
            SELECT f.*, v.distance
            FROM {VEC_FACT} v
            JOIN fact f ON f.id = v.fact_id
            WHERE v.embedding MATCH ?
              AND v.distance < ?
              AND k = ?
            """,
            (vec_blob, 1.0 - threshold, limit),
        ).fetchall()
        return [
            (_row_to_fact(row), 1.0 - row["distance"]) for row in rows
        ]

    def merge_facts(self, survivor_id: int, absorbed_ids: list[int]) -> None:
        """合并：survivor 的 source_message_ids union 所有 absorbed，mention_count 累加，删除 absorbed。"""
        if not absorbed_ids:
            return
        survivor = self.get_fact(survivor_id)
        if not survivor:
            return
        self._conn.execute("BEGIN")
        all_ids = set(survivor.source_message_ids)
        for aid in absorbed_ids:
            af = self.get_fact(aid)
            if af:
                all_ids.update(af.source_message_ids)
                survivor.mention_count += af.mention_count
            self._conn.execute("DELETE FROM fact WHERE id = ?", (aid,))
            self._conn.execute(f"DELETE FROM {VEC_FACT} WHERE fact_id = ?", (aid,))
            self._conn.execute(f"DELETE FROM {FTS_FACT} WHERE rowid = ?", (aid,))
        survivor.source_message_ids = list(all_ids)
        survivor.last_mentioned_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE fact SET source_message_ids = ?, mention_count = ?, last_mentioned_at = ? WHERE id = ?",
            (
                json.dumps(survivor.source_message_ids, ensure_ascii=False),
                survivor.mention_count,
                survivor.last_mentioned_at,
                survivor_id,
            ),
        )
        self._conn.commit()

    def all_fact_ids(self) -> list[int]:
        rows = self._conn.execute("SELECT id FROM fact").fetchall()
        return [r[0] for r in rows]

    def get_fact_embedding(self, fact_id: int) -> list[float] | None:
        row = self._conn.execute(
            f"SELECT embedding FROM {VEC_FACT} WHERE fact_id = ?", (fact_id,)
        ).fetchone()
        return _deserialize_embedding(row[0]) if row else None


# ── utils ──────────────────────────────────────────

def _serialize_embedding(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _deserialize_embedding(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _row_to_fact(row: sqlite3.Row) -> Fact:
    source_ids = []
    try:
        raw = row["source_message_ids"] if "source_message_ids" in row.keys() else "[]"
        if raw:
            source_ids = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        pass
    keys = row.keys()
    mention_count = row["mention_count"] if "mention_count" in keys else 1
    return Fact(
        id=row["id"],
        content=row["content"],
        source_message_ids=source_ids,
        created_at=row["created_at"],
        mention_count=mention_count,
        last_mentioned_at=row["last_mentioned_at"],
    )
