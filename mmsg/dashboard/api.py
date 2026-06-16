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
    consolidator: Any = None,
    merger: Any = None,
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
    async def list_messages(offset: int = 0, limit: int = 100) -> dict[str, Any]:
        rows, total = store.list_messages_paginated(offset=offset, limit=limit)
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
        return {"content": memory.knowledge.read() or ""}

    @app.put("/api/memory/knowledge")
    async def put_knowledge(body: dict[str, str]) -> dict[str, Any]:
        memory.knowledge.write(body.get("content", ""))
        return {"ok": True}

    @app.get("/api/memory/context")
    async def get_context() -> dict[str, str]:
        return {"content": memory.context.read() or ""}

    @app.put("/api/memory/context")
    async def put_context(body: dict[str, str]) -> dict[str, Any]:
        memory.context.write(body.get("content", ""))
        return {"ok": True}

    # ── Curiosity Notes ──────────────────────────────

    @app.get("/api/curiosity/notes")
    async def list_curiosity_notes() -> list[dict[str, Any]]:
        rows = store._conn.execute(
            "SELECT * FROM curiosity_note ORDER BY "
            "CASE status WHEN 'pending' THEN 1 WHEN 'pushed' THEN 2 ELSE 3 END, "
            "created_at DESC"
        ).fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            # 转换 needs_research int → bool 给前端
            d["needs_research"] = bool(d.get("needs_research", 0))
            results.append(d)
        return results

    @app.patch("/api/curiosity/notes/{note_id}")
    async def update_curiosity_note(note_id: int, body: dict[str, Any]) -> dict[str, Any]:
        allowed = {"status", "quality", "needs_research"}
        updates = {k: v for k, v in body.items() if k in allowed}
        if not updates:
            raise HTTPException(status_code=400, detail="no valid fields")
        if "needs_research" in updates and isinstance(updates["needs_research"], bool):
            updates["needs_research"] = int(updates["needs_research"])
        sets = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values())
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        store._conn.execute(
            f"UPDATE curiosity_note SET {sets}, updated_at = ? WHERE id = ?",
            [*values, now, note_id],
        )
        store._conn.commit()
        return {"ok": True}

    # ── Proactive 手动触发（调试用）─────────────────

    if proactive_engine is not None:

        @app.post("/api/curiosity/trigger-curiosity/{session_id}")
        async def trigger_curiosity(session_id: str) -> dict[str, Any]:
            """手动触发：从指定 session 生成 curiosity notes — 通过 Dashboard。"""
            log.info("[Dashboard] trigger_curiosity session=%s", session_id)
            try:
                count = await proactive_engine.trigger_curiosity(session_id)
                log.info("[Dashboard] trigger_curiosity done session=%s generated=%d", session_id, count)
                return {"ok": True, "generated": count}
            except Exception as e:
                log.exception("[Dashboard] trigger_curiosity failed session=%s", session_id)
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/api/curiosity/review-notes")
        async def trigger_review_curiosity() -> dict[str, Any]:
            """手动触发：立即整理 pending curiosity notes — 通过 Dashboard。"""
            log.info("[Dashboard] trigger_review_curiosity")
            try:
                result = await proactive_engine.trigger_review_curiosity()
                log.info("[Dashboard] trigger_review_curiosity done candidates=%d", result.get("count", 0))
                return {"ok": True, **result}
            except Exception as e:
                log.exception("[Dashboard] trigger_review_curiosity failed")
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/api/curiosity/simulate-push")
        async def simulate_push() -> dict[str, Any]:
            """模拟完整推送流程：整理→决策→生成消息，但不实际推送 — 通过 Dashboard。"""
            log.info("[Dashboard] simulate_push")
            try:
                result = await proactive_engine.simulate_push()
                log.info(
                    "[Dashboard] simulate_push done verdict=%s hours_since=%.1fh pushed_today=%d",
                    result.get("verdict"), result.get("hours_since_active", 0),
                    result.get("pushed_today", 0),
                )
                return {"ok": True, **result}
            except Exception as e:
                log.exception("[Dashboard] simulate_push failed")
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/api/curiosity/execute-push")
        async def execute_push() -> dict[str, Any]:
            """执行真实推送：整理→决策→推送 — 通过 Dashboard。"""
            log.info("[Dashboard] execute_push")
            try:
                result = await proactive_engine.execute_push()
                log.info(
                    "[Dashboard] execute_push done verdict=%s quiet=%s",
                    result.get("verdict"), result.get("quiet_hours", False),
                )
                return {"ok": True, **result}
            except Exception as e:
                log.exception("[Dashboard] execute_push failed")
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

    return app


async def start_dashboard(
    store: SqliteStore | None,
    memory: Any,
    host: str = "127.0.0.1",
    port: int = 9876,
    proactive_engine: Any = None,
    memory_curator: Any = None,
    consolidator: Any = None,
    merger: Any = None,
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

    app = _build_app(store, markdown, proactive_engine=proactive_engine, memory_curator=memory_curator, consolidator=consolidator, merger=merger)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    log.info("Dashboard → http://127.0.0.1:%d", port)
    await server.serve()
