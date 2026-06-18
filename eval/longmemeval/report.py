"""LongMemEval 报告产出 — JSON 全量 + Markdown 摘要。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import RETRIEVAL_KS, RRF_KS, MMR_KS


def build_report(
    results: list[dict],
    metrics: dict,
    metadata: dict,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "metadata": {
            **metadata,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        },
        "metrics": metrics,
        "questions": results,
    }

    (output_dir / "report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(
        _render_markdown(summary), encoding="utf-8"
    )


def _render_markdown(summary: dict) -> str:
    meta = summary["metadata"]
    overall = summary["metrics"].get("overall", {})
    by_type = {k: v for k, v in summary["metrics"].items() if k != "overall"}

    errors = sum(1 for r in summary["questions"] if r.get("error"))
    lines = [
        "# LongMemEval 召回 Metric 报告",
        "",
        f"- **数据**: {meta.get('data_path', '?')}",
        f"- **题数**: {meta.get('n_sampled', '?')} (有效 {meta.get('n_valid', '?')}, 异常 {errors})",
        f"- **模型**: {meta.get('model', '?')}",
        f"- **种子**: {meta.get('seed', '?')}",
        f"- **完成**: {meta.get('finished_at', '?')}",
        "",
    ]

    lines += _render_group("## Overall", overall)

    for qt, gm in sorted(by_type.items()):
        lines += _render_group(f"## {qt}", gm)

    # 错误题
    err_qs = [r for r in summary["questions"] if r.get("error")]
    if err_qs:
        lines += ["", "## 异常题目", ""]
        for r in err_qs:
            lines.append(f"- `{r['question_id']}` ({r['question_type']}): {r['error']}")

    return "\n".join(lines)


def _render_group(title: str, m: dict) -> list[str]:
    lines = ["", title, ""]

    n = m.get("n_total", 0)
    n_na = m.get("n_non_abstention", 0)
    n_ab = m.get("n_abstention", 0)
    lines.append(f"n={n} (非 abstention={n_na}, abstention={n_ab})")
    lines.append("")

    # 判别器
    disc = m.get("discriminator", {})
    lines.append("### 判别器")
    lines.append("")
    lines.append("| P | R | F1 | TP | FP | FN | TN |")
    lines.append("|---|---|----|----|----|----|-----|")
    lines.append(
        f"| {disc.get('precision', '—')} | {disc.get('recall', '—')} | {disc.get('f1', '—')}"
        f" | {disc.get('tp', '—')} | {disc.get('fp', '—')} | {disc.get('fn', '—')} | {disc.get('tn', '—')} |"
    )
    lines.append("")

    # Retrieval
    ret = m.get("retrieval", {})
    if ret:
        lines.append("### Retrieval")
        lines.append("")
        ks = [str(k) for k in RETRIEVAL_KS]
        lines.append("| " + " | ".join(f"Recall@{k}" for k in ks) + " |")
        lines.append("|" + "|".join("---" for _ in ks) + "|")
        lines.append("| " + " | ".join(str(ret.get(f"recall@{k}", "—")) for k in ks) + " |")
        lines.append("")

    # RRF
    rrf = m.get("rrf", {})
    if rrf:
        lines.append("### RRF")
        lines.append("")
        lines.append("| k | Recall@k | MRR@k | nDCG@k |")
        lines.append("|---|----------|-------|--------|")
        for k in RRF_KS:
            lines.append(
                f"| {k} | {rrf.get(f'recall@{k}', '—')} | {rrf.get(f'mrr@{k}', '—')} | {rrf.get(f'ndcg@{k}', '—')} |"
            )
        lines.append("")

    # MMR
    mmr = m.get("mmr", {})
    if mmr:
        lines.append("### MMR (最终输出)")
        lines.append("")
        lines.append("| k | Recall@k | Precision@k | MRR@k | nDCG@k |")
        lines.append("|---|----------|-------------|-------|--------|")
        for k in MMR_KS:
            lines.append(
                f"| {k} | {mmr.get(f'recall@{k}', '—')} | {mmr.get(f'precision@{k}', '—')}"
                f" | {mmr.get(f'mrr@{k}', '—')} | {mmr.get(f'ndcg@{k}', '—')} |"
            )
        lines.append("")

    # E2E
    e2e = m.get("e2e", {})
    if e2e:
        lines.append("### 端到端 Recall@5")
        lines.append("")
        lines.append("| realistic | oracle-gated |")
        lines.append("|-----------|--------------|")
        lines.append(
            f"| {e2e.get('realistic_recall@5', '—')} | {e2e.get('oracle_gated_recall@5', '—')} |"
        )
        lines.append("")

    # Abstention FP
    fp = m.get("abstention_fp_rate")
    if fp is not None:
        lines.append(f"**Abstention FP rate** (avg top-5 误召回 fact 数): {fp}")
        lines.append("")

    return lines
