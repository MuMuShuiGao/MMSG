from __future__ import annotations

import logging
import uuid

from ..bus.agent import AgentEvent, AgentBus
from ..bus.message import MESSAGE_INBOUND, MESSAGE_OUTBOUND, SESSION_RESET, MessageBus
from .models import Message
from .sqlite import SqliteStore

log = logging.getLogger("mmsg.storage")


class ChatRecorder:
    """订阅消息总线事件，自动将对话落库到 SQLite。"""

    def __init__(self, store: SqliteStore, agent_bus: AgentBus, message_bus: MessageBus) -> None:
        self._store = store
        self._agent_bus = agent_bus
        self._message_bus = message_bus
        self._session_id: str | None = None

    def install(self) -> None:
        self._message_bus.subscribe(MESSAGE_INBOUND, self._on_user_message)
        self._message_bus.subscribe(MESSAGE_OUTBOUND, self._on_outbound)
        self._message_bus.subscribe(SESSION_RESET, self._on_session_reset)
        self._agent_bus.subscribe(AgentEvent.AfterReasoning, self._on_llm_response)
        self._agent_bus.subscribe(AgentEvent.AfterToolCall, self._on_tool_result)

    # ---- session ----

    def _ensure_session(self) -> str:
        if self._session_id is None:
            self._session_id = uuid.uuid4().hex[:12]
            self._store.create_session(self._session_id)
            log.info("新会话: %s", self._session_id)
        return self._session_id

    async def _on_session_reset(self, evt) -> None:
        self._session_id = None

    # ---- inbound / outbound ----

    async def _on_user_message(self, evt) -> None:
        payload = evt.payload or {}
        text = payload.get("text", "")
        if not text:
            return
        sid = self._ensure_session()
        msg = Message(session_id=sid, role="user", content=text)
        self._store.save_message(msg)

    async def _on_outbound(self, evt) -> None:
        """最终回复文本由 Router 在 AgentLoop 完成后发送，这里作兜底记录。"""
        payload = evt.payload or {}
        text = payload.get("text", "")
        if not text or self._session_id is None:
            return
        msg = Message(session_id=self._session_id, role="assistant", content=text)
        self._store.save_message(msg)

    # ---- agent events ----

    async def _on_llm_response(self, evt) -> None:
        if self._session_id is None:
            return
        payload = evt.payload or {}
        content = payload.get("content", "")
        tool_calls = payload.get("tool_calls") or []
        meta = {}
        if payload.get("usage"):
            meta["usage"] = payload["usage"]
        if tool_calls:
            meta["tool_calls"] = tool_calls
        msg = Message(
            session_id=self._session_id,
            role="assistant",
            content=content,
            meta=meta,
        )
        self._store.save_message(msg)

    async def _on_tool_result(self, evt) -> None:
        if self._session_id is None:
            return
        payload = evt.payload or {}
        msg = Message(
            session_id=self._session_id,
            role="tool",
            content=str(payload.get("result", "")),
            meta={"tool_call_id": payload.get("id", ""), "name": payload.get("name", "")},
        )
        self._store.save_message(msg)
