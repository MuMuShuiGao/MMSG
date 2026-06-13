"""Dashboard FastAPI application — serves React SPA and REST API."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ..memory.engines.default.engine import DefaultMarkdownLayer
from ..memory.protocol import MemoryRuntime
from ..storage.sqlite import SqliteStore

log = logging.getLogger("mmsg.dashboard")

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _build_app(store: SqliteStore, memory: DefaultMarkdownLayer) -> FastAPI:
    app = FastAPI(title="MMSG Dashboard", version="0.1.0")

    # ── Static files (JS/CSS) ──────────────────────
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR)), name="assets")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # ── Sessions ────────────────────────────────────

    @app.get("/api/sessions")
    async def list_sessions(limit: int = 50) -> list[dict[str, Any]]:
        return store.list_sessions(limit=limit)

    @app.get("/api/sessions/{session_id}/messages")
    async def get_messages(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        msgs = store.get_messages(session_id, limit=limit)
        for m in msgs:
            meta_raw = m.get("meta")
            if isinstance(meta_raw, str):
                try:
                    m["meta"] = json.loads(meta_raw)
                except (json.JSONDecodeError, TypeError):
                    m["meta"] = {}
        return msgs

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict[str, str]:
        store.delete_session(session_id)
        return {"ok": True}

    @app.patch("/api/sessions/{session_id}")
    async def rename_session(session_id: str, body: dict[str, Any]) -> dict[str, str]:
        title = body.get("title", "")
        store._conn.execute(
            "UPDATE session SET title = ? WHERE id = ?", (title, session_id)
        )
        store._conn.commit()
        return {"ok": True}

    @app.patch("/api/messages/{msg_id}")
    async def update_message(msg_id: int, body: dict[str, Any]) -> dict[str, str]:
        content = body.get("content")
        if content is None:
            raise HTTPException(status_code=400, detail="content required")
        store.update_message(msg_id, content)
        return {"ok": True}

    @app.get("/api/usage/summary")
    async def usage_summary() -> dict[str, Any]:
        return store.usage_summary()

    # ── Memory ──────────────────────────────────────

    @app.get("/api/memory/knowledge")
    async def get_knowledge() -> dict[str, str]:
        return {"content": memory.knowledge.read() or ""}

    @app.put("/api/memory/knowledge")
    async def put_knowledge(body: dict[str, str]) -> dict[str, str]:
        memory.knowledge.write(body.get("content", ""))
        return {"ok": True}

    @app.get("/api/memory/context")
    async def get_context() -> dict[str, str]:
        return {"content": memory.context.read() or ""}

    @app.put("/api/memory/context")
    async def put_context(body: dict[str, str]) -> dict[str, str]:
        memory.context.write(body.get("content", ""))
        return {"ok": True}

    return app


async def start_dashboard(
    store: SqliteStore | None,
    memory: Any,
    host: str = "127.0.0.1",
    port: int = 9876,
) -> None:
    try:
        import uvicorn
    except ImportError:
        log.warning("uvicorn not installed, dashboard disabled. pip install uvicorn fastapi")
        return

    if not isinstance(memory, MemoryRuntime):
        log.warning("Dashboard requires MemoryRuntime, got %s. Memory tab disabled.", type(memory).__name__)
        return

    markdown = memory.markdown
    if not isinstance(markdown, DefaultMarkdownLayer):
        log.warning("Dashboard requires DefaultMarkdownLayer, got %s. Memory tab disabled.", type(markdown).__name__)
        return

    if store is None:
        log.warning("Dashboard requires SqliteStore. Sessions tab disabled.")
        return

    app = _build_app(store, markdown)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    log.info("Dashboard → http://%s:%d", host, port)
    await server.serve()
