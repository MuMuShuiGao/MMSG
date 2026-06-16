"""AgentLoop: 消费消息队列 → 委派 Reasoner → 持久化 → 发布出站。

职责：消息总线消费、组件装配、配置注入、会话管理、落库。
不懂"怎么多轮调工具"——那是 Reasoner 的事。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from ..bus.agent import AgentEvent, AgentBus
from ..bus.messagebus import MessageBus, SESSION_RESET
from ..llm.base import ChatMessage, LLMProvider, ToolCall
from ..memory import Memory
from ..tools.base import Tool
from ..storage.sqlite import SqliteStore
from ..prompt.segments import SystemPromptBuilder
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
        system_builder: SystemPromptBuilder | None = None,
        max_steps: int = 8,
        name: str = "agent",
        storage: SqliteStore | None = None,
        recaller = None,
    ) -> None:
        self.bus = agent_bus
        self.memory = memory
        self._message_bus = message_bus
        self.name = name
        self.storage = storage
        self._sessions: dict[str, str] = {}
        self._current_source: str = ""
        self.reasoner = Reasoner(
            llm=llm,
            bus=agent_bus,
            memory=memory,
            tools=tools or {},
            system_builder=system_builder,
            max_steps=max_steps,
            name=name,
        )
        self.reasoner._recaller = recaller

    # ── 消息队列消费 ──────────────────────────────

    async def serve(self) -> None:
        """长驻循环：从 message_bus 消费入站消息 → run() → 每步 publish 出站。"""
        if self._message_bus is None:
            raise RuntimeError("serve() 需要 message_bus")
        self._message_bus.events.subscribe(SESSION_RESET, self._on_session_reset)
        while True:
            item = await self._message_bus.consume_inbound()
            payload = item.payload or {}
            text = payload.get("text", "")
            if not text:
                continue

            out_base = {k: v for k, v in payload.items() if k != "text"}

            async for chunk in self.run(text, source=item.source):
                out_payload = {**out_base, "text": chunk.content, "done": chunk.done}
                await self._message_bus.publish_outbound(item.source, out_payload)

    # ── 会话管理 ──────────────────────────────────

    def _restore_latest_session(self, source: str) -> str | None:
        """按 source 查询最新 session，恢复消息历史到 reasoner._history。返回 session_id 或 None。"""
        if not self.storage:
            return None
        s = self.storage.get_session_by_source(source)
        if not s:
            return None
        sid = s["id"]
        rows = self.storage.get_messages(sid, limit=200)
        for row in rows:
            meta = row.get("meta") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            msg = ChatMessage(role=row["role"], content=row.get("content") or "")
            if row["role"] == "tool":
                msg.tool_call_id = meta.get("tool_call_id")
                msg.name = meta.get("name")
            elif row["role"] == "assistant":
                tcs_raw = meta.get("tool_calls") or []
                msg.tool_calls = [ToolCall(**tc) for tc in tcs_raw]
            self.reasoner._history.append(msg)
        log.info("恢复会话 %s (source=%s)，%d 条历史", sid, source, len(rows))
        return sid

    def _ensure_session(self, source: str) -> str:
        if self._sessions.get(source):
            return self._sessions[source]
        sid = self._restore_latest_session(source)
        if sid:
            self._sessions[source] = sid
            return sid
        sid = uuid.uuid4().hex[:12]
        self._sessions[source] = sid
        if self.storage:
            self.storage.create_session(sid, source=source)
        log.info("新会话: %s (source=%s)", sid, source)
        return sid

    async def _on_session_reset(self, evt: Any) -> None:
        self._sessions.clear()
        self.reasoner._history.clear()
        self.reasoner.context.reset_summary_state()

    # ── Turn 调度 ─────────────────────────────────

    async def run(self, user_input: str, source: str = "") -> AsyncGenerator[ThinkingResult, None]:
        """处理一次用户输入，逐步 yield ThinkingResult。

        done=False：中间 step；done=True：最终结果，此时落库并触发 AfterTurn。
        """
        self._current_source = source
        self._ensure_session(source)

        user_msg = ChatMessage(role="user", content=user_input)
        self.reasoner._history.append(user_msg)

        await self.bus.observe(AgentEvent.BeforeTurn, self.name, {})

        async for chunk in self.reasoner.think():
            if chunk.done:
                await self._persist_turn([user_msg] + chunk.records)
                await self.bus.observe(
                    AgentEvent.AfterTurn, self.name, {
                        "final": chunk.content,
                        "source": self._current_source,
                        "session_id": self._sessions.get(self._current_source, ""),
                    }
                )
            yield chunk

    # ── 持久化 ────────────────────────────────────

    async def _persist_turn(self, records: list[ChatMessage]) -> None:
        if not self.storage or not self._sessions.get(self._current_source):
            return
        sid = self._sessions[self._current_source]
        for rec in records:
            self.storage.save_message(sid, rec)
