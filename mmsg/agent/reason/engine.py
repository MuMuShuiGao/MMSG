"""推理引擎：完整的 ReAct 循环 — LLM 调用 + 流式收集 + 工具执行。

think() = prompt 拼装 → 多步推理 & 工具调用 → 写回 memory & 返回记录。
上下文组装和滑动窗口委托给 LLMContext。
AgentLoop 只负责消息总线消费和组件编排，不问"怎么多轮调工具"。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from pydantic import BaseModel, Field

from ...bus.agent import AgentEvent, AgentBus
from ...llm.base import ChatMessage, LLMProvider, ToolCall
from ...memory import Fact, Memory
from ...tools.base import Tool
from ...prompt.segments import SystemPromptBuilder
from ..context import LLMContext

log = logging.getLogger("mmsg.agent.reason")


class ReasoningResult(BaseModel):
    """单步推理结果。"""
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    step: int = 0


class ThinkingResult(BaseModel):
    """完整 ReAct 循环结果。done=False 表示中间 step，done=True 表示最终结果。"""
    content: str = ""
    records: list[ChatMessage] = Field(default_factory=list)
    steps: int = 0
    usage: dict[str, Any] = Field(default_factory=dict)
    done: bool = False


class Reasoner:
    """推理引擎：封装完整 ReAct 循环。

    职责：
    - 上下文窗口管理（memory recall → ChatMessage 组装）
    - 多步推理循环（LLM 调用 + AfterReasoning 拦截）
    - 工具执行（BeforeToolCall / AfterToolCall 事件）
    - 中间记录写回 memory
    - 返回 ThinkingResult 供上层持久化
    """

    def __init__(
        self,
        llm: LLMProvider,
        bus: AgentBus,
        memory: Memory,
        tools: dict[str, Tool],
        system_builder: SystemPromptBuilder | None = None,
        max_steps: int = 8,
        name: str = "agent",
        max_window: int = 40,
        llm_input_turns: int = 10,
        summarize_every: int = 5,
    ) -> None:
        self.llm = llm
        self.bus = bus
        self.memory = memory
        self.tools = tools
        self.max_steps = max_steps
        self.name = name
        self._history: list[ChatMessage] = []
        self.context = LLMContext(
            memory=memory,
            system_builder=system_builder,
            max_window=max_window,
            llm_input_turns=llm_input_turns,
            summarize_every=summarize_every,
        )
        self._recaller = None  # 由 AgentLoop 设置

    async def think(self) -> AsyncGenerator[ThinkingResult, None]:
        """执行完整 ReAct 循环，每个 step 完成后 yield 一个 ThinkingResult。

        done=False：中间 step 或 token 流式增量；done=True：最终结果，包含完整 records。
        进入循环前调一次 Recaller（判别 + hybrid 召回），多 step 共享同一份 facts。
        """
        records: list[ChatMessage] = []
        tool_schemas = [t.schema() for t in self.tools.values()] or None
        final_text = ""
        total_usage: dict[str, Any] = {}
        step = 0

        # 获取最后一条 user message 用于召回
        user_msg = ""
        for m in reversed(self._history):
            if m.role == "user":
                user_msg = m.content or ""
                break

        # 召回 facts（一次，多 step 共享）
        recall_facts: list[Fact] = []
        if self._recaller and user_msg:
            try:
                recall_facts = await self._recaller.recall_for_turn(user_msg)
            except Exception:
                log.exception("召回失败，降级为空")

        for step in range(1, self.max_steps + 1):
            msgs = await self.context.build(self._history, recall_facts)

            # 尝试流式执行本步，若 LLM 调用结束后没有 tool_calls 则说明是最终回复步，
            # 此时已经在 _stream_reason_step 里逐 token yield 过了；否则继续工具调用循环。
            final_text = ""
            collected_content = ""
            collected_tool_calls: list[ToolCall] = []
            finish_reason: str | None = None
            usage: dict[str, Any] = {}

            async for _item in self._stream_reason_step(msgs, tool_schemas, step):
                if isinstance(_item, str):
                    # token 增量
                    collected_content += _item
                    yield ThinkingResult(
                        content=collected_content,
                        records=list(records),
                        steps=step,
                        usage=total_usage,
                        done=False,
                    )
                else:
                    # ReasoningResult sentinel（流式结束信号）
                    collected_tool_calls = _item.tool_calls
                    finish_reason = _item.finish_reason
                    usage = _item.usage

            total_usage = usage or total_usage

            tc_dumps = [tc.model_dump() for tc in collected_tool_calls]
            usage_meta = {"tool_calls": tc_dumps}
            if usage:
                usage_meta["usage"] = usage
            self._history.append(
                ChatMessage(
                    role="assistant",
                    content=collected_content or "",
                    tool_calls=collected_tool_calls,
                )
            )
            records.append(
                ChatMessage(
                    role="assistant",
                    content=collected_content or "",
                    tool_calls=collected_tool_calls,
                    meta=usage_meta,
                )
            )

            if not collected_tool_calls:
                final_text = collected_content
                await self.bus.observe(
                    AgentEvent.AfterStep,
                    self.name,
                    {"step": step, "final": True, "text": final_text},
                )
                yield ThinkingResult(
                    content=final_text,
                    records=list(records),
                    steps=step,
                    usage=total_usage,
                    done=True,
                )
                return

            for tc in collected_tool_calls:
                await self.bus.observe(
                    AgentEvent.BeforeToolCall,
                    self.name,
                    {"step": step, "id": tc.id, "name": tc.name, "arguments": tc.arguments},
                )
                tool = self.tools.get(tc.name)
                if tool is None:
                    tool_result: Any = f"错误: 工具 '{tc.name}' 未注册"
                else:
                    try:
                        tool_result = await tool.run(**tc.arguments)
                    except Exception:
                        log.exception("工具 %s 执行失败", tc.name)
                        tool_result = f"错误: 工具 '{tc.name}' 执行异常"

                self._history.append(
                    ChatMessage(
                        role="tool",
                        content=str(tool_result),
                        tool_call_id=tc.id,
                        name=tc.name,
                    )
                )
                records.append(
                    ChatMessage(
                        role="tool",
                        content=str(tool_result),
                        tool_call_id=tc.id,
                        name=tc.name,
                        meta={"tool_call_id": tc.id, "name": tc.name},
                    )
                )
                await self.bus.observe(
                    AgentEvent.AfterToolCall,
                    self.name,
                    {"id": tc.id, "name": tc.name, "result": str(tool_result)},
                )

            await self.bus.observe(
                AgentEvent.AfterStep, self.name, {"step": step, "final": False}
            )
            yield ThinkingResult(
                content=collected_content or "",
                records=list(records),
                steps=step,
                usage=total_usage,
                done=False,
            )

        # max_steps 耗尽
        yield ThinkingResult(
            content=final_text, records=records, steps=step, usage=total_usage, done=True
        )

    # ── 内部 ──────────────────────────────────────

    async def _stream_reason_step(
        self,
        messages: list[ChatMessage],
        tool_schemas: list[dict[str, Any]] | None,
        step: int,
    ) -> AsyncGenerator[str | ReasoningResult, None]:
        """单步推理：逐 token yield str 增量，最后 yield ReasoningResult 作为结束信号。

        调用方通过 isinstance(_item, str) 区分 token 增量和结束信号。
        有 tool_calls 时不会 yield token（LLM 直接输出结构化调用，无文本）。
        """
        await self.bus.intercept(
            AgentEvent.BeforeStep,
            self.name,
            {
                "step": step,
                "messages": [m.model_dump() for m in messages],
                "tools": [t["function"]["name"] for t in (tool_schemas or [])],
            },
        )

        chunks = self.llm.chat_stream(messages, tools=tool_schemas)

        collected_content = ""
        collected_tool_calls: list[ToolCall] = []
        finish_reason: str | None = None
        usage: dict[str, Any] = {}

        async for chunk in chunks:
            if chunk.text:
                collected_content += chunk.text
                yield chunk.text
            if chunk.tool_calls:
                collected_tool_calls = chunk.tool_calls
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason
            if chunk.usage:
                usage = chunk.usage

        log.debug("stream step=%d content_len=%d finish=%s",
                  step, len(collected_content), finish_reason)

        resp_evt = await self.bus.intercept(
            AgentEvent.AfterReasoning,
            self.name,
            {
                "step": step,
                "content": collected_content,
                "tool_calls": [tc.model_dump() for tc in collected_tool_calls],
                "finish_reason": finish_reason,
                "usage": usage,
            },
        )

        collected_content = resp_evt.payload.get("content", collected_content)
        collected_tool_calls_raw: list[dict[str, Any]] = resp_evt.payload.get("tool_calls", [])
        raw_ids = {tc.get("id") for tc in collected_tool_calls_raw if isinstance(tc, dict)}
        collected_tool_calls = [
            tc for tc in collected_tool_calls if tc.id in raw_ids
        ]

        yield ReasoningResult(
            content=collected_content,
            tool_calls=collected_tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            step=step,
        )
