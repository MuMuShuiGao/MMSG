"""评分 — 字母 exact match。"""
from __future__ import annotations


def grade(predicted: str | None, gold: str) -> dict:
    """对比预测字母和正确答案。

    gold: 标准答案字母 (A/B/C/D)
    返回: {correct: bool, predicted, gold, reason}
    """
    g = gold.strip().upper()
    p = predicted.strip().upper() if predicted else None

    if p is None:
        return {
            "correct": False,
            "predicted": predicted,
            "gold": g,
            "reason": "未能从输出中提取有效字母",
        }

    if p == g:
        return {
            "correct": True,
            "predicted": p,
            "gold": g,
            "reason": "",
        }

    return {
        "correct": False,
        "predicted": p,
        "gold": g,
        "reason": f"预测 {p} != 标准答案 {g}",
    }
