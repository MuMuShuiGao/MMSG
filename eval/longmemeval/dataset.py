"""加载 LongMemEval JSON 文件，做 stratified 抽样。

LongMemEval JSON 格式（oracle/s/m 共用）：
[
  {
    "question_id": "q001",
    "question": "...",
    "answer": "...",
    "question_type": "single_session_user",   # 下划线形式
    "haystack_sessions": [
      {
        "session_id": "session_001",
        "date": "2023-01-15",          # 可选
        "conversation": [              # 有时也叫 "content"
          {"role": "user", "content": "..."},
          {"role": "assistant", "content": "..."}
        ]
      }
    ],
    "answer_session_ids": ["session_001"]  # 含证据的 session ID 列表
  },
  ...
]

注意：
- question_type 有时用连字符（"single-session-user"），统一转为下划线处理。
- haystack_sessions 里 turns 字段名可能是 "conversation" 或 "content"。
- answer_session_ids 可能是字符串列表或整数列表（整数 = 下标）。
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from .config import SKIP_QUESTION_TYPES


def load_longmemeval(
    data_path: str | Path,
    n: int = 10,
    seed: int = 42,
) -> list[dict]:
    """加载并 stratified 抽样 n 题（跳过 SKIP_QUESTION_TYPES）。

    返回归一化的 sample 列表：
    [{
        question_id, question, answer, question_type, is_abstention,
        haystack_sessions: [{session_id, turns: [{role, content}]}],
        answer_session_ids: [str],
    }, ...]
    """
    raw = json.loads(Path(data_path).read_text(encoding="utf-8"))

    samples: list[dict] = []
    for item in raw:
        qt = _normalize_qt(item.get("question_type", "unknown"))
        if qt in SKIP_QUESTION_TYPES:
            continue
        samples.append(_normalize_sample(item, qt))

    return _stratified_sample(samples, n, seed)


def _normalize_qt(qt: str) -> str:
    return qt.strip().lower().replace("-", "_")


def _normalize_sample(item: dict, qt: str) -> dict:
    from .config import ABSTENTION_QUESTION_TYPE

    # 真实格式：haystack_sessions = list[list[turn]]，session IDs 在 haystack_session_ids
    raw_sessions = item.get("haystack_sessions", [])
    session_ids = item.get("haystack_session_ids", [])
    haystack_sessions = _parse_haystack_sessions(raw_sessions, session_ids)

    answer_session_ids = [str(v) for v in item.get("answer_session_ids", [])]

    return {
        "question_id": str(item.get("question_id", "")),
        "question": item.get("question", item.get("query", "")),
        "answer": item.get("answer", ""),
        "question_type": qt,
        "is_abstention": (qt == ABSTENTION_QUESTION_TYPE or not answer_session_ids),
        "haystack_sessions": haystack_sessions,
        "answer_session_ids": answer_session_ids,
    }


def _parse_haystack_sessions(raw: list, session_ids: list) -> list[dict]:
    """统一 haystack session 结构为 [{session_id, turns}]。

    兼容两种格式：
    - list[list[turn]]：真实 LongMemEval 格式，session IDs 来自 haystack_session_ids
    - list[dict]：含 session_id + conversation/content/turns 字段的旧格式
    """
    result = []
    for i, s in enumerate(raw):
        if isinstance(s, list):
            # 真实格式
            sid = str(session_ids[i]) if i < len(session_ids) else f"session_{i}"
            turns = s
        elif isinstance(s, dict):
            sid = str(s.get("session_id", session_ids[i] if i < len(session_ids) else f"session_{i}"))
            turns = s.get("conversation") or s.get("content") or s.get("turns") or []
        else:
            continue
        result.append({
            "session_id": sid,
            "turns": [
                {"role": t.get("role", "user"), "content": t.get("content", "")}
                for t in turns
                if isinstance(t, dict) and t.get("content")
            ],
        })
    return result


def _stratified_sample(samples: list[dict], n: int, seed: int) -> list[dict]:
    """按 question_type 分层抽样，每层尽量均分。"""
    by_type: dict[str, list[dict]] = {}
    for s in samples:
        by_type.setdefault(s["question_type"], []).append(s)

    types = sorted(by_type.keys())
    rng = random.Random(seed)
    for lst in by_type.values():
        rng.shuffle(lst)

    result: list[dict] = []
    if not types:
        return result

    per_type = max(1, n // len(types))
    for qt in types:
        result.extend(by_type[qt][:per_type])

    # 若仍不足 n，从各 type 剩余里补
    extra = n - len(result)
    if extra > 0:
        remaining = [s for qt in types for s in by_type[qt][per_type:]]
        rng.shuffle(remaining)
        result.extend(remaining[:extra])

    return result[:n]
