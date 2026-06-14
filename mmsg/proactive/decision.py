"""推送决策：加权公式。"""
from __future__ import annotations

# 强度 → 静默阈值（小时）
INTENSITY_HOURS = {"strong": 2, "medium": 4, "weak": 24}

# 加权系数
ALPHA = 0.5   # 内容质量
BETA = 0.3    # 静默收益
GAMMA = 0.2   # 克制惩罚
BASELINE = 3.0


def silence_score(hours_since_last_active: float) -> int:
    """静默时长 → 1-5 分。"""
    if hours_since_last_active < 1:
        return 1
    if hours_since_last_active < 2:
        return 2
    if hours_since_last_active < 4:
        return 3
    if hours_since_last_active < 8:
        return 4
    return 5


def daily_penalty(pushed_today: int) -> int:
    """今日主动次数 → 1-5 分（分越高惩罚越低，越允许说话）。"""
    if pushed_today == 0:
        return 5
    if pushed_today == 1:
        return 3
    if pushed_today == 2:
        return 2
    return 1


def push_score(quality: float, hours_since_active: float, pushed_today: int) -> dict:
    """加权求和得分，返回完整诊断信息。"""
    sil = silence_score(hours_since_active)
    pen = daily_penalty(pushed_today)
    score = ALPHA * quality + BETA * sil + GAMMA * pen - BASELINE
    return {
        "score": round(score, 2),
        "would_push": score > 0,
        "quality": quality,
        "silence_score": sil,
        "daily_penalty": pen,
        "formula": f"0.5×{quality} + 0.3×{sil} + 0.2×{pen} - 3.0",
    }


def should_push(quality: float, hours_since_active: float, pushed_today: int) -> bool:
    """加权求和 > 0 则推送。"""
    return push_score(quality, hours_since_active, pushed_today)["would_push"]
