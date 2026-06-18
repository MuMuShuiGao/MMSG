"""画像收集链路：稀疏触发 → 现场扫 memory.md + PENDING.md → 单次 LLM 出问句 → 推送。"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from ..common import parse_json
from ..llm.base import ChatMessage, LLMProvider
from ..memory import Memory
from .ask_store import AskStore
from .prompts import PORTRAIT_PROMPT

log = logging.getLogger("mmsg.proactive.portrait")


class PortraitCollector:
    """画像收集链路。主循环每轮调 maybe_tick()，内部处理稀疏触发逻辑。"""

    _EVERY_N_TICKS = 8       # 每 8 轮主循环（≈ 2h）考虑一次
    _ENERGY_FULL_HOURS = 24  # silence 饱和点
    _RECENT_WINDOW_DAYS = 7  # warmth 统计窗口
    _RECENT_FULL_COUNT = 70  # warmth 饱和点（≈ 一天 10 条）
    _SCORE_THRESHOLD = 0.5
    _WEIGHT_SILENCE = 0.6    # warmth 权重 = 0.4
    _DEDUP_DAYS = 2          # 去重回溯窗口（天）：与近 N 天问过的内容比向量相似度
    _DEDUP_THRESHOLD = 0.85  # cosine 阈值：≥ 此值判定为重复，丢弃重生成
    _DEDUP_RETRY = 2         # 撞重后最多重生成次数
    _DAILY_CAP = 1           # 每天最多推送条数

    def __init__(
        self,
        llm: LLMProvider,
        memory: Memory,
        ask_store: AskStore,
        push_fn: Callable[[str], Awaitable[None]],
        hours_since_active_fn: Callable[[], float],
        in_quiet_hours_fn: Callable[[], bool],
        recent_count_fn: Callable[[int], int] | None = None,
    ) -> None:
        self._llm = llm
        self._memory = memory
        self._ask = ask_store
        self._push_fn = push_fn
        self._hours_since_active = hours_since_active_fn
        self._in_quiet_hours = in_quiet_hours_fn
        self._recent_count = recent_count_fn or (lambda _days: 0)

        self._tick: int = 0

    # ── 公共接口 ──────────────────────────────────

    async def maybe_tick(self) -> str | None:
        """主循环每轮调一次。满足所有门槛则跑画像链路，返回发出的消息或 None。"""
        self._tick += 1
        if self._tick % self._EVERY_N_TICKS != 0:
            return None

        result = await self._run(skip_gates=False)
        return result.get("message_sent")

    async def simulate(self) -> dict:
        """Dashboard 演练：跑完整链路但不真发，返回诊断（silence/warmth/score）+ 消息预览。"""
        result = await self._run(skip_gates=True, dry_run=True)
        return result

    async def execute(self) -> dict:
        """Dashboard 强制触发：跳过 K 轮 + 静默门槛，真发。"""
        result = await self._run(skip_gates=True, dry_run=False)
        return result

    # ── 内部流程 ──────────────────────────────────

    async def _run(self, skip_gates: bool = False, dry_run: bool = False) -> dict:
        hours_since = self._hours_since_active()
        quiet = self._in_quiet_hours()
        pushed_today = self._pushed_today()
        recent_msgs = self._recent_count(self._RECENT_WINDOW_DAYS)

        silence = min(1.0, hours_since / self._ENERGY_FULL_HOURS)
        warmth = min(1.0, recent_msgs / self._RECENT_FULL_COUNT)
        score = self._WEIGHT_SILENCE * silence + (1 - self._WEIGHT_SILENCE) * warmth

        result: dict[str, Any] = {
            "hours_since_active": round(hours_since, 1),
            "quiet_hours": quiet,
            "pushed_today": pushed_today,
            "recent_msgs": recent_msgs,
            "score_silence": round(silence, 3),
            "score_warmth": round(warmth, 3),
            "score": round(score, 3),
        }

        if not skip_gates:
            if quiet:
                result["verdict"] = "blocked_by_quiet_hours"
                return result
            if score < self._SCORE_THRESHOLD:
                result["verdict"] = "score_too_low"
                return result
            if pushed_today >= self._DAILY_CAP:
                result["verdict"] = "daily_cap_reached"
                return result
        else:
            result["gates_skipped"] = True

        gap = await self._generate_gap()
        result["gap_generated"] = gap

        if not gap or not gap.get("message"):
            result["verdict"] = "no_gap_found"
            return result

        message = gap["message"]
        topic_key = gap.get("topic_key", "")

        # 向量去重
        dedup_hit, embedding = await self._check_dedup(message)
        result["dedup_hit"] = dedup_hit

        if dedup_hit:
            result["verdict"] = "dedup_blocked"
            return result

        if dry_run:
            result["verdict"] = "would_push"
            result["message_preview"] = message
            result["topic_key"] = topic_key
            return result

        try:
            await self._push_fn(message)
        except Exception:
            log.exception("画像链路推送失败")
            result["verdict"] = "push_failed"
            return result

        await self._write_asked(message, topic_key, embedding)

        log.info("画像链路已推送: topic_key=%s msg=%s", topic_key, message[:60])
        result["verdict"] = "pushed"
        result["message_sent"] = message
        result["topic_key"] = topic_key
        return result

    async def _generate_gap(self) -> dict | None:
        """单次 LLM 调用，输出 {topic_key, message}。重试 dedup_retry 次。"""
        memory_text = self._memory.markdown.get_memory_context() or "（尚无画像记录）"
        pending_text = self._memory.markdown.read_pending() or "（无）"
        recent_asked = self._ask.get_recent_asked(days=self._DEDUP_DAYS)
        asked_list = self._format_asked_list(recent_asked)

        prompt = (
            PORTRAIT_PROMPT
            .replace("{memory}", memory_text)
            .replace("{pending}", pending_text)
            .replace("{dedup_days}", str(self._DEDUP_DAYS))
            .replace("{asked_list}", asked_list)
        )

        for attempt in range(max(1, self._DEDUP_RETRY + 1)):
            try:
                response = await self._llm.chat(
                    messages=[
                        ChatMessage(role="system", content=prompt),
                        ChatMessage(role="user", content="请输出 JSON。"),
                    ],
                )
                raw = (response.message.content or "").strip()
                data = parse_json(raw)
                if isinstance(data, dict) and data.get("message"):
                    dedup_hit, _ = await self._check_dedup(data["message"])
                    if not dedup_hit:
                        return data
                    log.debug("画像去重命中，第 %d 次重生成", attempt + 1)
                else:
                    log.debug("画像 LLM 输出空，跳过 attempt=%d", attempt)
                    return None
            except Exception:
                log.exception("画像 LLM 调用失败 attempt=%d", attempt)
                return None

        return None

    async def _check_dedup(self, message: str) -> tuple[bool, list[float] | None]:
        """检查 message 是否与近期 asked_question 相似。返回 (is_dup, embedding)。"""
        if self._memory.embed_provider is None:
            return False, None

        recent = self._ask.get_recent_asked(days=self._DEDUP_DAYS)
        if not recent:
            try:
                vecs = await self._memory.embed_provider.embed([message])
                return False, vecs[0]
            except Exception:
                log.exception("画像去重 embed 失败")
                return False, None

        texts = [message] + [r["content"] for r in recent]
        try:
            vecs = await self._memory.embed_provider.embed(texts)
        except Exception:
            log.exception("画像去重 embed 失败")
            return False, None

        new_vec = vecs[0]
        from ..memory.engines.default.vector_store import _deserialize_embedding

        for i, hist in enumerate(recent):
            if hist.get("embedding_blob"):
                hist_vec = _deserialize_embedding(hist["embedding_blob"])
            else:
                hist_vec = vecs[i + 1]

            cos = _cosine(new_vec, hist_vec)
            if cos >= self._DEDUP_THRESHOLD:
                log.debug("画像去重命中: cos=%.3f content=%s", cos, hist["content"][:40])
                return True, new_vec

        return False, new_vec

    async def _write_asked(
        self, message: str, topic_key: str, embedding: list[float] | None
    ) -> None:
        try:
            self._ask.save_asked(message, topic_key, embedding)
        except Exception:
            log.exception("写入 asked_question 失败")

    # ── 辅助 ─────────────────────────────────────

    def _pushed_today(self) -> int:
        return self._ask.get_asked_today_count()

    @staticmethod
    def _format_asked_list(recent: list[dict]) -> str:
        if not recent:
            return "（无）"
        lines = [f"- [{r['topic_key']}] {r['content']}" for r in recent]
        return "\n".join(lines)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
