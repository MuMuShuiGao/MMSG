"""AgentLoop: 消费消息队列 → 委派 Reasoner → 持久化 → 发布出站。

职责：消息总线消费、组件装配、配置注入、会话管理、落库。
不懂"怎么多轮调工具"——那是 Reasoner 的事。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from ..bus.agent import AgentEvent, AgentBus
from ..bus.messagebus import MessageBus, SESSION_RESET
from ..llm.base import ChatMessage, LLMProvider
from ..memory import Memory, MemoryRecord
from ..tools.base import Tool
from ..storage.models import Message, TurnRecord
from ..storage.sqlite import SqliteStore
from .reason import Reasoner
from .reason.engine import ThinkingResult

log = logging.getLogger("mmsg.agent")


class AgentLoop:
    def __init__(
        self,
        agent_bus: AgentBus,
        llm: LLMProvider,
        memory: Memory,
        tools: dict[str, Tool] | None = None,
        message_bus: MessageBus | None = None,
        system_prompt: str = "你是一个有用的助手。需要时请使用工具。",
        max_steps: int = 8,
        name: str = "agent",
        storage: SqliteStore | None = None,
    ) -> None:
        self.bus = agent_bus
        self.memory = memory
        self._message_bus = message_bus
        self.name = name
        self.storage = storage
        self._session_id: str | None = None
        self.reasoner = Reasoner(
            llm=llm,
            bus=agent_bus,
            memory=memory,
            tools=tools or {},
            system_prompt=system_prompt,
            max_steps=max_steps,
            name=name,
        )

    # ── 消息队列消费 ──────────────────────────────

    async def serve(self) -> None:
        """长驻循环：从 message_bus 消费入站消息 → run() → 每步 publish 出站。"""
        if self._message_bus is None:
            raise RuntimeError("serve() 需要 message_bus")
        self._restore_latest_session()
        self._message_bus.events.subscribe(SESSION_RESET, self._on_session_reset)
        while True:
            item = await self._message_bus.consume_inbound()
            payload = item.payload or {}
            text = payload.get("text", "")
            if not text:
                continue

            out_base: dict = {}
            if payload.get("openid"):
                out_base["openid"] = payload["openid"]

            async for chunk in self.run(text):
                out_payload = {**out_base, "text": chunk.content, "done": chunk.done}
                await self._message_bus.publish_outbound(item.source, out_payload)

    # ── 会话管理 ──────────────────────────────────

    def _restore_latest_session(self) -> None:
        """进程启动时：恢复最新 session 及其消息历史到 reasoner._history。"""
        if not self.storage:
            return
        sessions = self.storage.list_sessions(limit=1)
        if not sessions:
            return
        sid = sessions[0]["id"]
        self._session_id = sid
        rows = self.storage.get_messages(sid, limit=200)
        for row in rows:
            meta = row.get("meta") or {}
            if isinstance(meta, str):
                import json as _json
                try:
                    meta = _json.loads(meta)
                except Exception:
                    meta = {}
            msg = ChatMessage(role=row["role"], content=row.get("content") or "")
            if row["role"] == "tool":
                msg.tool_call_id = meta.get("tool_call_id")
                msg.name = meta.get("name")
            elif row["role"] == "assistant":
                tcs_raw = meta.get("tool_calls") or []
                from ..llm.base import ToolCall
                msg.tool_calls = [ToolCall(**tc) for tc in tcs_raw]
            self.reasoner._history.append(msg)
        log.info("恢复会话 %s，%d 条历史", sid, len(rows))

    def _ensure_session(self) -> str:
        if self._session_id is None:
            self._session_id = uuid.uuid4().hex[:12]
            if self.storage:
                self.storage.create_session(self._session_id)
            log.info("新会话: %s", self._session_id)
        return self._session_id

    async def _on_session_reset(self, evt: Any) -> None:
        self._session_id = None
        self.reasoner._history.clear()
        self.reasoner._last_summarized_turn = 0

    # ── Turn 调度 ─────────────────────────────────

    async def run(self, user_input: str) -> AsyncGenerator[ThinkingResult, None]:
        """处理一次用户输入，逐步 yield ThinkingResult。

        done=False：中间 step；done=True：最终结果，此时落库并触发 AfterTurn。
        """
        self._ensure_session()

        user_record = MemoryRecord(role="user", content=user_input)
        await self.memory.write(user_record)
        self.reasoner._history.append(ChatMessage(role="user", content=user_input))
        user_tr = TurnRecord(role="user", content=user_input)

        await self.bus.observe(AgentEvent.BeforeTurn, self.name, {})

        async for chunk in self.reasoner.think():
            if chunk.done:
                await self._persist_turn([user_tr] + chunk.records)
                await self.bus.observe(
                    AgentEvent.AfterTurn, self.name, {"final": chunk.content}
                )
            yield chunk

    # ── 持久化 ────────────────────────────────────

    async def _persist_turn(self, records: list[TurnRecord]) -> None:
        if not self.storage or not self._session_id:
            return
        for rec in records:
            self.storage.save_message(
                Message(
                    session_id=self._session_id,
                    role=rec.role,
                    content=rec.content,
                    meta=rec.meta,
                )
            )
