from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .models import Session
from ..llm.base import ChatMessage


class SessionMixin:
    _conn: sqlite3.Connection

    # ---- session ----

    def create_session(self, session_id: str, source: str = "", title: str = "") -> Session:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO session (id, source, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, source, title, now, now),
        )
        self._conn.commit()
        return Session(id=session_id, source=source, title=title, created_at=now, updated_at=now)

    def touch_session(self, session_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE session SET updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        self._conn.commit()

    def get_session_by_source(self, source: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM session WHERE source = ? ORDER BY updated_at DESC LIMIT 1",
            (source,),
        ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, limit: int = 20, source: str | None = None) -> list[dict[str, Any]]:
        if source is not None:
            rows = self._conn.execute(
                "SELECT * FROM session WHERE source = ? ORDER BY updated_at DESC LIMIT ?",
                (source, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM session ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_session(self, session_id: str) -> None:
        self._conn.execute("DELETE FROM message WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM session WHERE id = ?", (session_id,))
        self._conn.commit()

    # ---- message ----

    def save_message(self, session_id: str, msg: ChatMessage) -> int:
        now = datetime.now(timezone.utc).isoformat()
        seq = self._next_seq(session_id)
        cur = self._conn.execute(
            "INSERT INTO message (session_id, seq, role, content, meta, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, seq, msg.role, msg.content or "", json.dumps(msg.meta, ensure_ascii=False), now),
        )
        self._conn.commit()
        self.touch_session(session_id)
        return cur.lastrowid

    def _next_seq(self, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM message WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row[0] + 1

    def get_messages(
        self, session_id: str, limit: int = 100, before_id: int | None = None
    ) -> list[dict[str, Any]]:
        if before_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM message WHERE session_id = ? AND id < ? ORDER BY seq DESC LIMIT ?",
                (session_id, before_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM message WHERE session_id = ? ORDER BY seq DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        rows.reverse()
        return [dict(r) for r in rows]

    def list_messages_paginated(
        self, offset: int = 0, limit: int = 100, role: str | None = None, q: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = []
        params: list[Any] = []
        if role:
            conditions.append("m.role = ?")
            params.append(role)
        if q:
            conditions.append("m.content LIKE ?")
            params.append(f"%{q}%")
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        total = self._conn.execute(
            f"SELECT COUNT(*) FROM message m{where}", params
        ).fetchone()[0]
        rows = self._conn.execute(
            f"SELECT m.*, s.title as session_title, s.source as session_source "
            f"FROM message m LEFT JOIN session s ON m.session_id = s.id"
            f"{where} ORDER BY m.id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows], total

    def get_user_messages_since(self, since_id: int) -> list[dict[str, Any]]:
        """consolidator 用：取 id > since_id 的 role='user' 消息。"""
        rows = self._conn.execute(
            "SELECT id, content, created_at FROM message WHERE id > ? AND role = 'user' ORDER BY id ASC",
            (since_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_messages_since(self, since_id: int, roles: list[str] | None = None) -> list[dict[str, Any]]:
        """SelfCurator 用：取 id > since_id 的双方消息，roles 为空则取全部。"""
        if roles:
            placeholders = ",".join("?" * len(roles))
            rows = self._conn.execute(
                f"SELECT id, role, content, created_at FROM message "
                f"WHERE id > ? AND role IN ({placeholders}) ORDER BY id ASC",
                [since_id, *roles],
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, role, content, created_at FROM message WHERE id > ? ORDER BY id ASC",
                (since_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_user_messages_since(self, since_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM message WHERE id > ? AND role = 'user'",
            (since_id,),
        ).fetchone()
        return row[0] if row else 0

    def get_recent_user_message_ids(self, limit: int = 50) -> list[int]:
        """反刍检测用：取最近 N 条 user message id（不含 content）。"""
        rows = self._conn.execute(
            "SELECT id FROM message WHERE role = 'user' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [r[0] for r in rows]

    def update_message(self, msg_id: int, content: str) -> None:
        self._conn.execute(
            "UPDATE message SET content = ? WHERE id = ?",
            (content, msg_id),
        )
        self._conn.commit()

    def get_message(self, msg_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM message WHERE id = ?", (msg_id,)
        ).fetchone()
        return dict(row) if row else None
