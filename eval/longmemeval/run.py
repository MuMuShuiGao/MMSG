"""LongMemEval 召回评测入口 — python -m eval.longmemeval.run"""
from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from mmsg.config import llm as _llm_cfg
from mmsg.core import llm_registry
from mmsg.llm import OpenAIProvider
from mmsg.llm.embedding import create_embedding_provider

from .dataset import load_longmemeval
from .metrics import compute_metrics
from .report import build_report
from .runner import run_one_question

log = logging.getLogger("mmsg.eval.longmemeval")

RESULTS_ROOT = Path(__file__).resolve().parent.parent / "results"


def _register_plugins() -> None:
    llm_registry.register("openai")(OpenAIProvider)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LongMemEval 召回评测")
    p.add_argument("--data-path", required=True,
                   help="LongMemEval JSON 文件路径（如 eval/longmemeval/data/longmemeval_oracle.json）")
    p.add_argument("--n", type=int, default=10,
                   help="stratified 抽样题数（默认 10）")
    p.add_argument("--seed", type=int, default=42,
                   help="随机种子（默认 42）")
    return p.parse_args()


async def main() -> None:
    args = parse_args()

    _register_plugins()

    logging.basicConfig(level=logging.WARNING)
    log.setLevel(logging.INFO)
    logging.getLogger("mmsg.eval.longmemeval.runner").setLevel(logging.INFO)
    logging.getLogger("mmsg.eval.longmemeval.ingest").setLevel(logging.INFO)

    log.info("加载数据集: %s (n=%d, seed=%d)", args.data_path, args.n, args.seed)
    samples = load_longmemeval(args.data_path, n=args.n, seed=args.seed)
    log.info("抽样完成，共 %d 题", len(samples))

    llm = OpenAIProvider(timeout=300.0)
    embed = create_embedding_provider()
    if embed is None:
        log.warning("embedding provider 未配置，召回 metric 将全部为 0")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = RESULTS_ROOT / f"{timestamp}_longmemeval"
    output_dir.mkdir(parents=True, exist_ok=True)

    base_temp = Path(tempfile.mkdtemp(prefix="longmemeval_"))
    t_start = time.perf_counter()

    all_results: list[dict] = []
    errors = 0

    for i, sample in enumerate(samples):
        log.info("[%d/%d] %s", i + 1, len(samples), sample["question_id"])
        try:
            result = await run_one_question(sample, llm, base_temp, embedding_provider=embed)
            if result.get("error"):
                errors += 1
            all_results.append(result)
        except Exception as exc:
            log.exception("题目 %s 异常: %s", sample["question_id"], exc)
            errors += 1

    elapsed = int(time.perf_counter() - t_start)
    log.info("全部完成: %d 题 / 异常 %d / %ds", len(all_results), errors, elapsed)

    metrics = compute_metrics(all_results)

    metadata = {
        "data_path": args.data_path,
        "n_sampled": args.n,
        "n_valid": sum(1 for r in all_results if not r.get("error")),
        "seed": args.seed,
        "model": _llm_cfg("model"),
        "embed_enabled": embed is not None,
        "duration_seconds": elapsed,
    }

    build_report(all_results, metrics, metadata, output_dir)
    log.info("报告已产出: %s", output_dir)

    shutil.rmtree(base_temp, ignore_errors=True)

    # 打印 overall 摘要到 stdout
    overall = metrics.get("overall", {})
    mmr5 = overall.get("mmr", {})
    e2e = overall.get("e2e", {})
    print(f"\n=== Overall ===")
    print(f"  MMR  Recall@5: {mmr5.get('recall@5')}  MRR@5: {mmr5.get('mrr@5')}  nDCG@5: {mmr5.get('ndcg@5')}")
    print(f"  E2E  realistic@5: {e2e.get('realistic_recall@5')}  oracle-gated@5: {e2e.get('oracle_gated_recall@5')}")
    disc = overall.get("discriminator", {})
    print(f"  Disc F1: {disc.get('f1')}  P: {disc.get('precision')}  R: {disc.get('recall')}")
    print(f"  报告: {output_dir}")


if __name__ == "__main__":
    asyncio.run(main())
