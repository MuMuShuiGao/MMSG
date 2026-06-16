from __future__ import annotations

import sqlite3


class StateMixin:
    _conn: sqlite3.Connection

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
