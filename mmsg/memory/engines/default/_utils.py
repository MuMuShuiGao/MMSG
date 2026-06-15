"""memory 引擎共享工具。"""
from __future__ import annotations

import json
from typing import Any


def parse_json(raw: str) -> dict | list | None:
    """从 LLM 输出中提取 JSON。"""
    content = raw.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        content = "\n".join(lines).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None
