"""curiosity_note 的 SQLite CRUD。"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from mmsg.storage.models import CuriosityNote


class NoteStore:
    def __init__(self, conn) -> None:
        self._conn = conn

    # ---- write ----

    def save_notes(self, notes: list[CuriosityNote]) -> list[int]:
        now = datetime.now(timezone.utc).isoformat()
        ids: list[int] = []
        for note in notes:
            cur = self._conn.execute(
                """INSERT INTO curiosity_note
                   (session_id, content, category, quality, needs_research, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    note.session_id,
                    note.content,
                    note.category,
                    note.quality,
                    int(note.needs_research),
                    note.status,
                    now,
                    now,
                ),
            )
            ids.append(cur.lastrowid)
        self._conn.commit()
        return ids

    def update_note(self, note_id: int, **fields) -> None:
        if not fields:
            return
        now = datetime.now(timezone.utc).isoformat()
        # 处理 bool → int for needs_research
        if "needs_research" in fields and isinstance(fields["needs_research"], bool):
            fields["needs_research"] = int(fields["needs_research"])
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [now, note_id]
        self._conn.execute(
            f"UPDATE curiosity_note SET {sets}, updated_at = ? WHERE id = ?",
            values,
        )
        self._conn.commit()

    def mark_pushed(self, note_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE curiosity_note SET status = 'pushed', triggered_at = ?, updated_at = ? WHERE id = ?",
            (now, now, note_id),
        )
        self._conn.commit()

    def dismiss_note(self, note_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE curiosity_note SET status = 'dismissed', updated_at = ? WHERE id = ?",
            (now, note_id),
        )
        self._conn.commit()

    # ---- read ----

    def get_pending_notes(self) -> list[CuriosityNote]:
        rows = self._conn.execute(
            "SELECT * FROM curiosity_note WHERE status = 'pending' ORDER BY quality DESC, created_at DESC"
        ).fetchall()
        return [self._row_to_note(dict(r)) for r in rows]

    def get_pushed_today_count(self) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self._conn.execute(
            "SELECT COUNT(*) FROM curiosity_note WHERE status = 'pushed' AND triggered_at >= ?",
            (today,),
        ).fetchone()
        return row[0] if row else 0

    def get_last_active_at(self) -> str | None:
        row = self._conn.execute(
            "SELECT MAX(created_at) FROM message"
        ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _row_to_note(row: dict) -> CuriosityNote:
        return CuriosityNote(
            id=row["id"],
            session_id=row.get("session_id"),
            content=row["content"],
            category=row.get("category", "curiosity"),
            quality=row.get("quality", 3),
            needs_research=bool(row.get("needs_research", 0)),
            status=row.get("status", "pending"),
            triggered_at=row.get("triggered_at"),
            merged_from=row.get("merged_from"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
