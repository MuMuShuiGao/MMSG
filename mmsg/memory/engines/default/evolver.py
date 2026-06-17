"""Evolver worker：消化 PENDING.md → LLM 智能合并到 memory.md + 选择性更新 self.md。

独立 serve() 循环，12h 或 PENDING ≥1000 字触发。
完成后全量清空 PENDING.md。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from mmsg.llm.base import ChatMessage, LLMProvider
from mmsg.common import parse_json, parse_datetime_utc, hours_elapsed, in_quiet_hours
from mmsg.memory.protocol import MarkdownMemoryLayer

log = logging.getLogger("mmsg.memory.evolver")

DEFAULT_MIN_HOURS = 12
DEFAULT_MIN_CHARS = 1000

MEMORY_EVOLVE_SYSTEM_PROMPT = """你是用户的长期记忆管理助手。

你的任务是将 PENDING.md 中的增量信息与现有 memory.md 合并，输出更新后的完整画像。

## 输入
- 现有画像：memory.md 全文
- 待消化：PENDING.md 全文

## 输出
返回一个 JSON，只输出 JSON：
{
  "memory": "更新后的完整画像（markdown）",
  "note": "一句话说明本次变更"
}

## 画像格式
分节小标题，每条用 \"- \" 开头：

## 身份与项目
- ...

## 偏好
- ...

## 生活与关系
- ...

## 长期关注
- ...

## 规则
- 去重：如果 pending 中的事实与现有画像重复，合并为一条，保留更精确的版本
- 冲突解决：以 `更正` 开头的事实覆盖已有画像中的对应旧值
- 归类：把新事实放入对应章节
- 时效性：纯短期状态（如\"今天心情好\"）不写入
- 去噪音：与长期画像无关的临时对话信息舍去
- 只提取稳定事实和长期偏好
- 总字数控制在 {max_chars} 字以内，超出则合并相近条目或删除过时内容
"""

SELF_EVOLVE_SYSTEM_PROMPT = """你现在扮演 MMSG。

你的任务是检视 PENDING.md 中的增量信息，判断是否有值得吸收进 self.md 的内容。

## 输入
- 现有自我认知：self.md 全文
- 待消化：PENDING.md 全文

## 输出
返回一个 JSON，只输出 JSON：
{
  "self": "更新后的完整自我认知（markdown），无变化则返回原样",
  "note": "一句话说明本次变更或无变化原因"
}

## self.md 格式（不可变更）
必须且只能包含三个章节：

## 关于我自己
（我的特质、边界、我坚持什么）

## 我眼中的用户
（我感受到的用户印象——非客观事实，主观认知）

## 我们的相处关系
（互动模式、关系阶段、共同语言）

## 规则
- 大多数 pending 事实与 self 无关，直接忽略，不强行更新
- 只吸收影响\"我眼中用户\"、\"相处模式\"、\"我的自我认知\"这三类的信息
- 保持原风格和长度
- 章节标题固定不变
"""


class Evolver:
    """消化 PENDING → 合并到主文件。"""

    def __init__(
        self,
        store,                       # SqliteStore
        llm: LLMProvider,
        markdown: MarkdownMemoryLayer,
        memory_max_chars: int = 4000,
        min_hours: int = DEFAULT_MIN_HOURS,
        min_chars: int = DEFAULT_MIN_CHARS,
        poll_interval: int = 3600,
    ) -> None:
        self._store = store
        self._llm = llm
        self._markdown = markdown
        self._memory_max_chars = memory_max_chars
        self._min_hours = min_hours
        self._min_chars = min_chars
        self._poll_interval = poll_interval
        self._quiet_start = "00:00"
        self._quiet_end = "07:00"

    # ── state 属性 ──────────────────────────────────

    @property
    def _last_run_at(self) -> str | None:
        return self._store.get_memory_state("evolver_last_run_at")

    @_last_run_at.setter
    def _last_run_at(self, val: str) -> None:
        self._store.set_memory_state("evolver_last_run_at", val)

    # ── 主循环 ──────────────────────────────────────

    async def serve(self) -> None:
        log.info("Evolver worker 已启动 min_hours=%d min_chars=%d",
                 self._min_hours, self._min_chars)

        while True:
            await asyncio.sleep(self._poll_interval)
            if in_quiet_hours(self._quiet_start, self._quiet_end):
                continue
            if not self._should_run():
                continue
            try:
                await self._evolve()
            except Exception:
                log.exception("Evolver 消化失败")

    def _should_run(self) -> bool:
        pending = self._read_pending()
        if not pending:
            return False

        if len(pending) >= self._min_chars:
            return True

        last_run = self._last_run_at
        if last_run:
            try:
                last_dt = parse_datetime_utc(last_run)
                hours_since = hours_elapsed(last_dt)
            except (TypeError, ValueError):
                hours_since = 999
            return hours_since >= self._min_hours

        return True

    # ── 消化逻辑 ────────────────────────────────────

    async def _evolve(self) -> None:
        pending = self._read_pending()
        if not pending:
            return

        now = datetime.now(timezone.utc).isoformat()
        log.info("Evolver 开始消化: pending_chars=%d", len(pending))

        memory_ok = await self._merge_memory(pending)
        self_ok = await self._update_self(pending)

        if memory_ok or self_ok:
            self._clear_pending()
        self._last_run_at = now

        log.info("Evolver 消化完成: memory_ok=%s self_ok=%s", memory_ok, self_ok)

    async def _merge_memory(self, pending: str) -> bool:
        current_memory = self._markdown.get_memory_context() or ""
        system_prompt = MEMORY_EVOLVE_SYSTEM_PROMPT.replace(
            "{max_chars}", str(self._memory_max_chars)
        )

        response = await self._llm.chat(
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(
                    role="user",
                    content=(
                        f"## 现有画像\n\n{current_memory}\n\n"
                        f"## 待消化\n\n{pending}"
                    ),
                ),
            ],
        )

        raw = response.message.content or ""
        data = parse_json(raw)

        if data is None:
            log.warning("memory merge JSON 解析失败，跳过本次合并")
            return False

        if data.get("memory"):
            self._markdown.write_memory(data["memory"])
            log.info("memory.md 更新完成: chars=%d", len(data["memory"]))
            return True

        log.info("memory.md 无变更")
        return True

    async def _update_self(self, pending: str) -> bool:
        current_self = self._markdown.get_self_context() or ""

        response = await self._llm.chat(
            messages=[
                ChatMessage(role="system", content=SELF_EVOLVE_SYSTEM_PROMPT),
                ChatMessage(
                    role="user",
                    content=(
                        f"## 现有自我认知\n\n{current_self}\n\n"
                        f"## 待消化\n\n{pending}"
                    ),
                ),
            ],
        )

        raw = response.message.content or ""
        data = parse_json(raw)

        if data is None:
            log.warning("self update JSON 解析失败，跳过本次更新")
            return False

        if data.get("self"):
            self._markdown.write_self(data["self"])
            log.info("self.md 更新完成: chars=%d note=%s",
                     len(data["self"]), data.get("note", ""))
            return True

        log.info("self.md 无变更")
        return True

    # ── PENDING 文件操作 ────────────────────────────

    def _read_pending(self) -> str | None:
        return self._markdown.read_pending()

    def _clear_pending(self) -> None:
        self._markdown.clear_pending()

    # ── 手动触发（dashboard 用）─────────────────────

    async def trigger_evolve(self) -> dict:
        log.info("Dashboard | 手动触发 Evolver…")
        try:
            await self._evolve()
            return {"ok": True}
        except Exception as e:
            log.exception("Dashboard | Evolver 失败")
            return {"ok": False, "error": str(e)}

    def get_state(self) -> dict:
        return {
            "last_run_at": self._last_run_at,
        }
