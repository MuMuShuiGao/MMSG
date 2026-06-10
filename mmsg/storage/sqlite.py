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
                title      TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS message (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES session(id),
                role       TEXT NOT NULL,
                content    TEXT NOT NULL DEFAULT '',
                meta       TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_msg_session
                ON message(session_id, id);
        """)
        self._conn.commit()

    # ---- session ----

    def create_session(self, session_id: str, title: str = "") -> Session:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO session (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title, now, now),
        )
        self._conn.commit()
        return Session(id=session_id, title=title, created_at=now, updated_at=now)

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
        cur = self._conn.execute(
            "INSERT INTO message (session_id, role, content, meta, created_at) VALUES (?, ?, ?, ?, ?)",
            (msg.session_id, msg.role, msg.content, json.dumps(msg.meta, ensure_ascii=False), now),
        )
        self._conn.commit()
        self.touch_session(msg.session_id)
        return cur.lastrowid

    def get_messages(
        self, session_id: str, limit: int = 100, before_id: int | None = None
    ) -> list[dict[str, Any]]:
        if before_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM message WHERE session_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
                (session_id, before_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM message WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        rows.reverse()
        return [dict(r) for r in rows]

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM session ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
