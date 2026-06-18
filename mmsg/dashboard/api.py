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


def _build_app(
    store: SqliteStore,
    memory: DefaultMarkdownLayer,
    proactive_engine: Any = None,
    memory_curator: Any = None,
    evolver: Any = None,
    consolidator: Any = None,
    merger: Any = None,
    tool_registry: Any = None,
) -> FastAPI:
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
    async def delete_session(session_id: str) -> dict[str, Any]:
        store.delete_session(session_id)
        return {"ok": True}

    # ── Messages ────────────────────────────────────

    @app.get("/api/messages")
    async def list_messages(
        offset: int = 0,
        limit: int = 100,
        role: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        rows, total = store.list_messages_paginated(
            offset=offset, limit=limit, role=role, q=q,
        )
        for m in rows:
            meta_raw = m.get("meta")
            if isinstance(meta_raw, str):
                try:
                    m["meta"] = json.loads(meta_raw)
                except (json.JSONDecodeError, TypeError):
                    m["meta"] = {}
        return {"rows": rows, "total": total}

    @app.patch("/api/sessions/{session_id}")
    async def rename_session(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        title = body.get("title", "")
        store._conn.execute(
            "UPDATE session SET title = ? WHERE id = ?", (title, session_id)
        )
        store._conn.commit()
        return {"ok": True}

    @app.get("/api/usage/summary")
    async def usage_summary() -> dict[str, Any]:
        return store.usage_summary()

    # ── Memory ──────────────────────────────────────

    @app.get("/api/memory/knowledge")
    async def get_knowledge() -> dict[str, str]:
        return {"content": memory.get_memory_context() or ""}

    @app.put("/api/memory/knowledge")
    async def put_knowledge(body: dict[str, str]) -> dict[str, Any]:
        memory.write_memory(body.get("content", ""))
        return {"ok": True}

    @app.get("/api/memory/context")
    async def get_context() -> dict[str, str]:
        return {"content": memory.read_recent_context() or ""}

    @app.put("/api/memory/context")
    async def put_context(body: dict[str, str]) -> dict[str, Any]:
        # TODO: 补 write_recent_context() 公开方法，避免直接访问 private .context
        memory.context.write(body.get("content", ""))
        return {"ok": True}

    @app.get("/api/memory/self")
    async def get_self() -> dict[str, str]:
        return {"content": memory.get_self_context() or ""}

    @app.put("/api/memory/self")
    async def put_self(body: dict[str, str]) -> dict[str, Any]:
        memory.write_self(body.get("content", ""))
        return {"ok": True}

    # ── Proactive 手动触发（调试用）─────────────────

    if proactive_engine is not None:

        @app.get("/api/portrait/status")
        async def portrait_status() -> dict[str, Any]:
            """当前画像触发状态 + 下次推送预测。"""
            try:
                return proactive_engine.portrait_status()
            except Exception as e:
                log.exception("[Dashboard] portrait_status failed")
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/api/portrait/simulate")
        async def simulate_portrait() -> dict[str, Any]:
            """演练画像链路，不真发。"""
            log.info("[Dashboard] simulate_portrait")
            try:
                result = await proactive_engine.simulate_portrait()
                return {"ok": True, **result}
            except Exception as e:
                log.exception("[Dashboard] simulate_portrait failed")
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/api/portrait/execute")
        async def execute_portrait() -> dict[str, Any]:
            """强制触发画像链路，真发。"""
            log.info("[Dashboard] execute_portrait")
            try:
                result = await proactive_engine.execute_portrait()
                return {"ok": True, **result}
            except Exception as e:
                log.exception("[Dashboard] execute_portrait failed")
                raise HTTPException(status_code=500, detail=str(e))

    # ── Memory Curator ──────────────────────────

    if memory_curator is not None:

        @app.post("/api/memory/curate")
        async def trigger_memory_curate() -> dict[str, Any]:
            log.info("[Dashboard] trigger_memory_curate")
            try:
                result = await memory_curator.trigger_curate()
                return result
            except Exception as e:
                log.exception("[Dashboard] trigger_memory_curate failed")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/memory/state")
        async def get_memory_state() -> dict[str, Any]:
            return memory_curator.get_state()

    # ── Evolver ──────────────────────────────────

    if evolver is not None:

        @app.post("/api/memory/evolve")
        async def trigger_evolve() -> dict[str, Any]:
            log.info("[Dashboard] trigger_evolve")
            try:
                result = await evolver.trigger_evolve()
                return result
            except Exception as e:
                log.exception("[Dashboard] trigger_evolve failed")
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/api/memory/evolver-state")
        async def get_evolver_state() -> dict[str, Any]:
            return evolver.get_state()

    @app.get("/api/memory/pending")
    async def get_pending() -> dict[str, str]:
        return {"content": memory.read_pending() or ""}

    @app.delete("/api/memory/pending")
    async def clear_pending() -> dict[str, Any]:
        memory.clear_pending()
        return {"ok": True}

    # ── Consolidator / Merger 状态 ─────────────────

    @app.get("/api/memory/workers-state")
    async def get_workers_state() -> dict[str, Any]:
        result: dict[str, Any] = {}
        if consolidator is not None:
            try:
                result["consolidator"] = consolidator.get_state()
            except Exception:
                result["consolidator"] = None
        if merger is not None:
            try:
                result["merger"] = merger.get_state()
            except Exception:
                result["merger"] = None
        return result

    # ── Tools ──────────────────────────────────────

    if tool_registry is not None:

        @app.get("/api/tools")
        async def list_tools() -> list[dict[str, Any]]:
            return tool_registry.list_meta()

        @app.patch("/api/tools/{name}")
        async def set_tool_enabled(name: str, body: dict[str, Any]) -> dict[str, Any]:
            enabled = bool(body.get("enabled", True))
            if not tool_registry.set_enabled(name, enabled):
                raise HTTPException(status_code=404, detail=f"tool '{name}' not found")
            return {"ok": True, "name": name, "enabled": enabled}

    return app


async def start_dashboard(
    store: SqliteStore | None,
    memory: Any,
    host: str = "127.0.0.1",
    port: int = 9876,
    proactive_engine: Any = None,
    memory_curator: Any = None,
    evolver: Any = None,
    consolidator: Any = None,
    merger: Any = None,
    tool_registry: Any = None,
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

    app = _build_app(store, markdown, proactive_engine=proactive_engine, memory_curator=memory_curator, evolver=evolver, consolidator=consolidator, merger=merger, tool_registry=tool_registry)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    log.info("Dashboard → http://127.0.0.1:%d", port)
    await server.serve()
