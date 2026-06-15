"""项目级公共工具函数。"""
from __future__ import annotations

import json
from datetime import datetime, timezone


def parse_json(raw: str) -> dict | list | None:
    """从 LLM 输出中提取 JSON（自动去除 ``` 代码围栏）。"""
    content = raw.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        lines = [line for line in lines if not line.startswith("```")]
        content = "\n".join(lines).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


def parse_datetime_utc(iso_str: str) -> datetime:
    """解析 ISO 格式时间字符串，若无时区则假定 UTC。"""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def hours_elapsed(dt: datetime) -> float:
    """返回 dt 到 now(UTC) 的小时差。"""
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def days_elapsed(dt: datetime) -> float:
    """返回 dt 到 now(UTC) 的天数差。"""
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def in_quiet_hours(quiet_start: str, quiet_end: str) -> bool:
    """检查当前时间是否在静默时段内（支持跨午夜，如 23:00-07:00）。"""
    if quiet_start == quiet_end:
        return False
    now = datetime.now().strftime("%H:%M")
    if quiet_start <= quiet_end:
        return quiet_start <= now < quiet_end
    else:
        return now >= quiet_start or now < quiet_end
