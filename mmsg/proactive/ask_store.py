"""asked_question 表的 CRUD。画像链路推送后的事件 log + 2 天向量去重索引。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..storage.schema import VEC_ASKED


class AskStore:
    def __init__(self, conn) -> None:
        self._conn = conn

    # ── write ──────────────────────────────────────

    def save_asked(
        self,
        content: str,
        topic_key: str,
        embedding: list[float] | None = None,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "INSERT INTO asked_question (content, topic_key, asked_at) VALUES (?, ?, ?)",
            (content, topic_key, now),
        )
        row_id: int = cur.lastrowid
        if embedding:
            from ..memory.engines.default.vector_store import _serialize_embedding
            self._conn.execute(
                f"INSERT INTO {VEC_ASKED} (asked_question_id, embedding) VALUES (?, ?)",
                (row_id, _serialize_embedding(embedding)),
            )
        self._conn.commit()
        return row_id

    # ── read ───────────────────────────────────────

    def get_asked_today_count(self) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self._conn.execute(
            "SELECT COUNT(*) FROM asked_question WHERE asked_at >= ?",
            (today,),
        ).fetchone()
        return row[0] if row else 0

    def get_recent_asked(self, days: int = 2) -> list[dict]:
        """返回近 N 天内 asked_question 行（含 embedding blob）。"""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """
            SELECT aq.id, aq.content, aq.topic_key, aq.asked_at,
                   vaq.embedding
            FROM asked_question aq
            LEFT JOIN vec_asked_question vaq ON vaq.asked_question_id = aq.id
            WHERE aq.asked_at >= ?
            ORDER BY aq.asked_at DESC
            """,
            (cutoff,),
        ).fetchall()
        result = []
        for r in rows:
            result.append(
                {
                    "id": r[0],
                    "content": r[1],
                    "topic_key": r[2],
                    "asked_at": r[3],
                    "embedding_blob": r[4],
                }
            )
        return result
