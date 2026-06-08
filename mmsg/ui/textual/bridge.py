"""BusBridge：订阅 EventBus，翻译事件为 Textual Message。

服务端事件通过 transport.raw 到达 → Event.from_json 反序列化 → 按类型分发为 Textual Message。
只有本模块同时接触 EventBus 和 Textual。widgets 永不 import EventBus。
"""

from __future__ import annotations

import logging

from textual.app import App

from ...core import events as E
from ...core.bus import Event, EventBus
from .messages import (
    AgentFinal,
    AgentStart,
    AgentTokenDelta,
    ClearScreen,
    StatusChange,
    ToolCallError,
    ToolCallResult,
    ToolCallStart,
)

log = logging.getLogger("mmsg.bridge")


class BusBridge:
    def __init__(self, bus: EventBus, app: App) -> None:
        self._bus = bus
        self._app = app
        self._current_step = 0

    def install(self) -> None:
        """注册 transport.raw 订阅，将服务端推送的原始 JSON 反序列化后分发。"""
        self._bus.subscribe(E.TRANSPORT_RAW, self._on_raw)

    # ─── 反序列化 & 分发 ────────────────────────────

    async def _on_raw(self, evt: Event) -> None:
        """收到传输层推送的原始 JSON 行，反序列化后按事件类型分发。"""
        data = evt.payload.get("data", "")
        if not data:
            return
        try:
            remote_evt = Event.from_json(data)
        except Exception:
            log.debug("无法解析传输数据: %r", data)
            return

        # 按远程事件类型分发到对应 handler
        t = remote_evt.type
        if t == E.LOOP_STEP:
            await self._on_loop_step(remote_evt)
        elif t == E.LLM_TOKEN:
            await self._on_llm_token(remote_evt)
        elif t == E.TOOL_CALL:
            await self._on_tool_call(remote_evt)
        elif t == E.TOOL_RESULT:
            await self._on_tool_result(remote_evt)
        elif t == E.TOOL_ERROR:
            await self._on_tool_error(remote_evt)
        elif t == E.AGENT_FINAL:
            await self._on_agent_final(remote_evt)
        elif t == E.LLM_ERROR:
            await self._on_llm_error(remote_evt)
        elif t == E.SESSION_RESET:
            await self._on_session_reset(remote_evt)

    # ─── 各事件 handler ─────────────────────────────

    async def _on_loop_step(self, evt: Event) -> None:
        step = evt.payload.get("step", 0)
        self._current_step = step
        self._app.post_message(AgentStart(step))
        self._app.post_message(StatusChange("思考中..."))

    async def _on_llm_token(self, evt: Event) -> None:
        text = evt.payload.get("text", "")
        if text:
            self._app.post_message(AgentTokenDelta(text))

    async def _on_tool_call(self, evt: Event) -> None:
        p = evt.payload
        self._app.post_message(
            ToolCallStart(
                step=p.get("step", 0),
                tool_id=p.get("id", ""),
                name=p.get("name", ""),
                arguments=p.get("arguments", {}),
            )
        )
        self._app.post_message(StatusChange(f"执行工具 {p.get('name', '')}..."))

    async def _on_tool_result(self, evt: Event) -> None:
        p = evt.payload
        self._app.post_message(
            ToolCallResult(
                tool_id=p.get("id", ""),
                name=p.get("name", ""),
                result=p.get("result", ""),
            )
        )

    async def _on_tool_error(self, evt: Event) -> None:
        p = evt.payload
        self._app.post_message(
            ToolCallError(
                tool_id=p.get("id", ""),
                name=p.get("name", ""),
                error=p.get("error", ""),
            )
        )

    async def _on_agent_final(self, evt: Event) -> None:
        self._app.post_message(AgentFinal(evt.payload.get("text", "")))
        self._app.post_message(StatusChange("就绪"))

    async def _on_llm_error(self, evt: Event) -> None:
        err = evt.payload.get("error", "")
        self._app.post_message(StatusChange(f"错误: {err}"))

    async def _on_session_reset(self, evt: Event) -> None:
        self._app.post_message(ClearScreen())
