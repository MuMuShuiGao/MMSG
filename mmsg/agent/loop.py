"""AgentLoop: 消费消息队列 → 委派 Reasoner → 持久化 → 发布出站。

职责：消息总线消费、组件装配、配置注入、会话管理、落库。
不懂"怎么多轮调工具"——那是 Reasoner 的事。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ..bus.agent import AgentEvent, AgentBus
from ..bus.messagebus import MessageBus, SESSION_RESET
from ..llm.base import LLMProvider
from ..memory import Memory, MemoryRecord
from ..tools.base import Tool
from ..storage.models import Message, TurnRecord
from ..storage.sqlite import SqliteStore
from .reason import Reasoner

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
        """长驻循环：从 message_bus 消费入站消息 → run() → 发布出站。"""
        if self._message_bus is None:
            raise RuntimeError("serve() 需要 message_bus")
        self._message_bus.events.subscribe(SESSION_RESET, self._on_session_reset)
        while True:
            item = await self._message_bus.consume_inbound()
            payload = item.payload or {}
            text = payload.get("text", "")
            if not text:
                continue
            result = await self.run(text)

            out_payload: dict = {"text": result}
            if payload.get("openid"):
                out_payload["openid"] = payload["openid"]
            await self._message_bus.publish_outbound(item.source, out_payload)

    # ── 会话管理 ──────────────────────────────────

    def _ensure_session(self) -> str:
        if self._session_id is None:
            self._session_id = uuid.uuid4().hex[:12]
            if self.storage:
                self.storage.create_session(self._session_id)
            log.info("新会话: %s", self._session_id)
        return self._session_id

    async def _on_session_reset(self, evt: Any) -> None:
        self._session_id = None

    # ── Turn 调度 ─────────────────────────────────

    async def run(self, user_input: str) -> str:
        """处理一次用户输入：写 memory → 委派 Reasoner → 持久化。

        不懂工具调用细节，只把 Reasoner 返回的记录落库。
        """
        self._ensure_session()
        await self.memory.start_turn()

        user_record = MemoryRecord(role="user", content=user_input)
        await self.memory.write(user_record)
        user_tr = TurnRecord(role="user", content=user_input)

        await self.bus.observe(AgentEvent.BeforeTurn, self.name, {})

        result = await self.reasoner.think()

        await self._persist_turn([user_tr] + result.records)
        await self.memory.end_turn(user_input, result.content[:200])
        await self.bus.observe(
            AgentEvent.AfterTurn, self.name, {"final": result.content}
        )
        return result.content

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
