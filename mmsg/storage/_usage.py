from __future__ import annotations

import json
import sqlite3
from typing import Any


class UsageMixin:
    _conn: sqlite3.Connection

    def usage_summary(self) -> dict[str, Any]:
        rows = self._conn.execute(
            "SELECT session_id, meta FROM message WHERE role = 'assistant'"
        ).fetchall()
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
