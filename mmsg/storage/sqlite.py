from __future__ import annotations

import sqlite3
from pathlib import Path

try:
    import sqlite_vec
except ImportError:
    raise ImportError(
        "sqlite-vec 未安装，请运行：pip install sqlite-vec\n"
        "或重新安装项目：pip install -e ."
    )

from .schema import init_schema
from ._sessions import SessionMixin
from ._state import StateMixin
from ._usage import UsageMixin


class SqliteStore(SessionMixin, StateMixin, UsageMixin):
    def __init__(self, db_path: str | Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        vec_db_path = db_path.with_suffix(".vec.db")
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        init_schema(self._conn, vec_db_path)

    def close(self) -> None:
        self._conn.close()
