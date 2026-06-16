from __future__ import annotations

import logging
import sqlite3

log = logging.getLogger("mmsg.storage")

_DDL = """
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
    topic_key      TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS fact (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    content            TEXT NOT NULL,
    source_message_ids TEXT NOT NULL DEFAULT '[]',
    created_at         TEXT NOT NULL,
    mention_count      INTEGER NOT NULL DEFAULT 1,
    last_mentioned_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fact_created_at ON fact(created_at DESC);
"""

_VIRTUAL_TABLES: list[tuple[str, str]] = [
    ("vec_fact", """
CREATE VIRTUAL TABLE IF NOT EXISTS vec_fact USING vec0(
    fact_id   INTEGER PRIMARY KEY,
    embedding FLOAT[1024]
);
"""),
    ("fts_fact", """
CREATE VIRTUAL TABLE IF NOT EXISTS fts_fact USING fts5(
    content,
    tokenize='unicode61'
);
"""),
    ("vec_message", """
CREATE VIRTUAL TABLE IF NOT EXISTS vec_message USING vec0(
    message_id INTEGER PRIMARY KEY,
    embedding  FLOAT[1024]
);
"""),
]


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    for name, ddl in _VIRTUAL_TABLES:
        try:
            conn.executescript(ddl)
        except Exception:
            log.warning("%s 虚表创建跳过（可能已存在）", name)
    # 兼容已有库：补 topic_key 列（列存在时 SQLite 会报错，忽略即可）
    try:
        conn.execute("ALTER TABLE curiosity_note ADD COLUMN topic_key TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    conn.commit()
