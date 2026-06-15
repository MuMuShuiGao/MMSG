from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlite_vec

from .models import MemoryState, Message, Session

log = logging.getLogger("mmsg.storage")


class SqliteStore:
    def __init__(self, db_path: str | Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._init_tables()
        self._run_migrations()

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

            CREATE TABLE IF NOT EXISTS curiosity_note (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id     TEXT,
                content        TEXT NOT NULL DEFAULT '',
                category       TEXT NOT NULL DEFAULT 'curiosity',
                quality        INTEGER NOT NULL DEFAULT 3,
                needs_research INTEGER NOT NULL DEFAULT 0,
                status         TEXT NOT NULL DEFAULT 'pending',
                triggered_at   TEXT,
                merged_from    TEXT,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_note_status
                ON curiosity_note(status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS memory_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );
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

    def get_user_messages_since(self, since_id: int) -> list[dict[str, Any]]:
        """consolidator 用：取 id > since_id 的 role='user' 消息。"""
        rows = self._conn.execute(
            "SELECT id, content FROM message WHERE id > ? AND role = 'user' ORDER BY id ASC",
            (since_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_user_messages_since(self, since_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM message WHERE id > ? AND role = 'user'",
            (since_id,),
        ).fetchone()
        return row[0] if row else 0

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

    # ---- schema migrations ----

    _MIGRATIONS: list[str] = [
        # v1: fact / vec_fact / fts_fact tables
        """
CREATE TABLE IF NOT EXISTS fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT NOT NULL,
  source_message_ids TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  mention_count INTEGER NOT NULL DEFAULT 1,
  last_mentioned_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fact_created_at ON fact(created_at DESC);

-- Try virtual tables; they are idempotent via IF NOT EXISTS-like behavior
-- sqlite-vec vec0: won't error if exists, but we guard with a schema_version check
""",
    ]

    _MIGRATION_VEC: str = """
CREATE VIRTUAL TABLE IF NOT EXISTS vec_fact USING vec0(
  fact_id INTEGER PRIMARY KEY,
  embedding FLOAT[1024]
);
"""

    _MIGRATION_FTS: str = """
CREATE VIRTUAL TABLE IF NOT EXISTS fts_fact USING fts5(
  content,
  tokenize='unicode61'
);
"""

    def _run_migrations(self) -> None:
        cur_ver = self._schema_version
        if cur_ver < 1:
            self._conn.executescript(self._MIGRATIONS[0])
            try:
                self._conn.executescript(self._MIGRATION_VEC)
            except Exception:
                log.warning("vec_fact 虚表创建失败（可能已存在），跳过")
            try:
                self._conn.executescript(self._MIGRATION_FTS)
            except Exception:
                log.warning("fts_fact 虚表创建失败（可能已存在），跳过")
            self._schema_version = 1
        self._conn.commit()

    @property
    def _schema_version(self) -> int:
        r = self._conn.execute(
            "SELECT value FROM memory_state WHERE key = 'schema_version'"
        ).fetchone()
        return int(r["value"]) if r else 0

    @_schema_version.setter
    def _schema_version(self, val: int) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO memory_state (key, value) VALUES ('schema_version', ?)",
            (str(val),),
        )

    # ---- memory_state ----

    def get_memory_state(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM memory_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_memory_state(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO memory_state (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()
