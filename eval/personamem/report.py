"""报告产出 — JSON 逐题 + Markdown 摘要 + 自动 diff 上一次同 tier 结果。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_report(
    results: list[dict],
    metadata: dict,
    output_dir: Path,
) -> None:
    """产出 report.json 和 report.md。

    results: [{question_id, predicted, gold, correct, question_type, usage, raw, ...}, ...]
    metadata: {tier, seed, concurrency, model, memory_backend, sample_count, ...}
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    total = len(results)
    correct = sum(1 for r in results if r.get("correct"))
    accuracy = correct / total if total > 0 else 0.0

    by_type: dict[str, dict] = {}
    for r in results:
        qt = r.get("question_type") or "unknown"
        bucket = by_type.setdefault(qt, {"total": 0, "correct": 0})
        bucket["total"] += 1
        if r.get("correct"):
            bucket["correct"] += 1

    type_breakdown = {
        qt: {
            "count": b["total"],
            "correct": b["correct"],
            "accuracy": round(b["correct"] / b["total"], 4) if b["total"] > 0 else 0.0,
        }
        for qt, b in sorted(by_type.items())
    }

    total_tokens = _sum_tokens(results)
    prev = _load_previous_report(metadata["tier"], output_dir.parent)

    summary = {
        "metadata": {
            **metadata,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": metadata.get("duration_seconds", 0),
        },
        "overall": {
            "total": total,
            "correct": correct,
            "accuracy": round(accuracy, 4),
            "total_tokens": total_tokens,
        },
        "by_question_type": type_breakdown,
        "questions": results,
    }

    # -- 写 JSON --
    json_path = output_dir / "report.json"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # -- 写 Markdown --
    md_path = output_dir / "report.md"
    md_path.write_text(_render_markdown(summary, prev), encoding="utf-8")


def _render_markdown(summary: dict, prev: dict | None) -> str:
    meta = summary["metadata"]
    overall = summary["overall"]

    lines = [
        f"# PersonaMem 评测报告",
        "",
        f"- **轮次**: {meta.get('tier', '?')}",
        f"- **样本数**: {meta.get('sample_count', '?')}",
        f"- **种子**: {meta.get('seed', '?')}",
        f"- **模型**: {meta.get('model', '?')}",
        f"- **记忆后端**: {meta.get('memory_backend', '?')}",
        f"- **并发**: {meta.get('concurrency', '?')}",
        f"- **完成时间**: {meta.get('finished_at', '?')}",
        f"- **总 Token**: {overall.get('total_tokens', {})}",
        "",
        "## 总准确率",
        "",
        f"| 指标 | 本次 | 上次 | 差异 |",
        f"|------|------|------|------|",
    ]

    prev_overall = prev.get("overall", {}) if prev else {}
    prev_acc = prev_overall.get("accuracy")
    curr_acc = overall["accuracy"]

    diff_str = f"{(curr_acc - prev_acc):+.2%}" if prev_acc is not None else "—"
    prev_str = f"{prev_acc:.2%}" if prev_acc is not None else "—"
    lines.append(
        f"| 准确率 | {curr_acc:.2%} | {prev_str} | {diff_str} |"
    )

    lines.extend(["", "## 按问题类型", ""])
    lines.append("| 类型 | 数量 | 准确率 | 上次 | 差异 |")
    lines.append("|------|------|--------|------|------|")

    for qt, bucket in summary["by_question_type"].items():
        prev_bucket = (prev.get("by_question_type", {}).get(qt) if prev else {}) or {}
        prev_acc_type = prev_bucket.get("accuracy")
        curr_acc_type = bucket["accuracy"]
        diff_type = f"{(curr_acc_type - prev_acc_type):+.2%}" if prev_acc_type is not None else "—"
        prev_type_str = f"{prev_acc_type:.2%}" if prev_acc_type is not None else "—"
        lines.append(
            f"| {qt} | {bucket['count']} | {curr_acc_type:.2%} | {prev_type_str} | {diff_type} |"
        )

    fail_count = overall["total"] - overall["correct"]
    if fail_count > 0:
        lines.extend(["", "## 错误样例", ""])
        for q in summary["questions"]:
            if not q.get("correct"):
                lines.append(
                    f"- `{q.get('question_id', '?')}` ({q.get('question_type', '?')}): "
                    f"预测 `{q.get('predicted', '?')}`  ≠  `{q.get('gold', '?')}`"
                )

    return "\n".join(lines)


def _load_previous_report(tier: str, results_root: Path) -> dict | None:
    """加载上一次同 tier 的 report.json（按时间倒序，排除本次自身）。"""
    matched = sorted(
        results_root.glob(f"*_{tier}/report.json"),
        reverse=True,
    )
    # 排除最新的（正在写的那个）
    for p in matched[1:]:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _sum_tokens(results: list[dict]) -> dict:
    """汇总所有题目的 token 用量。"""
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for r in results:
        usage = r.get("usage") or {}
        for k in total:
            val = usage.get(k, 0)
            if isinstance(val, int | float):
                total[k] += int(val)
    return total
