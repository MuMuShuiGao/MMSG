from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger("mmsg.storage")

VEC_ATTACH = "vec"
VEC_FACT = f"{VEC_ATTACH}.vec_fact"
FTS_FACT = f"{VEC_ATTACH}.fts_fact"
VEC_MESSAGE = f"{VEC_ATTACH}.vec_message"
VEC_ASKED = f"{VEC_ATTACH}.vec_asked_question"

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

CREATE TABLE IF NOT EXISTS asked_question (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    content   TEXT NOT NULL DEFAULT '',
    topic_key TEXT NOT NULL DEFAULT '',
    asked_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_asked_question_asked_at
    ON asked_question(asked_at DESC);

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
    ("vec_fact", f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {VEC_FACT} USING vec0(
    fact_id   INTEGER PRIMARY KEY,
    embedding FLOAT[1024]
);
"""),
    ("fts_fact", f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_FACT} USING fts5(
    content,
    tokenize='unicode61'
);
"""),
    ("vec_message", f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {VEC_MESSAGE} USING vec0(
    message_id INTEGER PRIMARY KEY,
    embedding  FLOAT[1024]
);
"""),
    ("vec_asked_question", f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {VEC_ASKED} USING vec0(
    asked_question_id INTEGER PRIMARY KEY,
    embedding         FLOAT[1024]
);
"""),
]


def init_schema(conn: sqlite3.Connection, vec_db_path: str | Path) -> None:
    conn.execute(f"ATTACH DATABASE ? AS {VEC_ATTACH}", (str(vec_db_path),))
    conn.executescript(_DDL)
    for name, ddl in _VIRTUAL_TABLES:
        try:
            conn.executescript(ddl)
        except Exception:
            log.warning("%s 虚表创建跳过（可能已存在）", name)
    conn.commit()
