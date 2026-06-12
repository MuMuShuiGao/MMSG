from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Message, Session


class SqliteStore:
    def __init__(self, db_path: str | Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS session (
                id         TEXT PRIMARY KEY,
                source     TEXT NOT NULL DEFAULT '',
                title      TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_session_source
                ON session(source, updated_at DESC);

            CREATE TABLE IF NOT EXISTS message (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES session(id),
                seq        INTEGER NOT NULL DEFAULT 0,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL DEFAULT '',
                meta       TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_msg_session
                ON message(session_id, seq);
        """)
        self._conn.commit()

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

    # ---- message ----

    def save_message(self, msg: Message) -> int:
        now = msg.created_at or datetime.now(timezone.utc).isoformat()
        seq = msg.seq or self._next_seq(msg.session_id)
        cur = self._conn.execute(
            "INSERT INTO message (session_id, seq, role, content, meta, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (msg.session_id, seq, msg.role, msg.content, json.dumps(msg.meta, ensure_ascii=False), now),
        )
        self._conn.commit()
        self.touch_session(msg.session_id)
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

    def usage_summary(self) -> dict[str, Any]:
        rows = self._conn.execute("SELECT session_id, meta FROM message WHERE role = 'assistant'").fetchall()
        total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        by_session: dict[str, dict[str, Any]] = {}

        for row in rows:
            try:
                meta = json.loads(row["meta"] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            usage = meta.get("usage") or {}
            if not isinstance(usage, dict):
                continue

            session_id = row["session_id"]
            session = by_session.setdefault(
                session_id,
                {"session_id": session_id, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
            for key in total:
                value = usage.get(key) or 0
                if isinstance(value, int | float):
                    total[key] += int(value)
                    session[key] += int(value)

        sessions = sorted(by_session.values(), key=lambda item: item["total_tokens"], reverse=True)
        return {"total": total, "sessions": sessions}

    def close(self) -> None:
        self._conn.close()
