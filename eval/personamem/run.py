"""PersonaMem 评测入口 — python -m eval.personamem.run"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import tempfile
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

from mmsg.config import llm as _llm_cfg
from mmsg.core import llm_registry
from mmsg.llm import OpenAIProvider

from .config import TIERS
from .dataset import load_personamem
from .report import build_report
from .runner import run_one_sample

log = logging.getLogger("mmsg.eval.personamem")

RESULTS_ROOT = Path(__file__).resolve().parent.parent / "results"


def _register_plugins() -> None:
    llm_registry.register("openai")(OpenAIProvider)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PersonaMem 记忆评测")
    p.add_argument("--tier", choices=list(TIERS), default="smoke",
                   help="评测量级 (默认 smoke)")
    p.add_argument("--seed", type=int, default=42,
                   help="随机种子 (默认 42)")
    p.add_argument("--concurrency", type=int, default=None,
                   help="并发数 (默认按 tier 预设)")
    p.add_argument("--context-size", choices=["32k", "128k", "1M"], default="32k",
                   help="上下文长度切片 (默认 32k)")
    return p.parse_args()


async def main() -> None:
    args = parse_args()

    _register_plugins()

    tier_cfg = TIERS[args.tier]
    concurrency = args.concurrency or tier_cfg.default_concurrency

    logging.basicConfig(level=logging.WARNING)
    log.setLevel(logging.INFO)

    log.info("加载数据集 (tier=%s, seed=%d, context=%s)...",
             args.tier, args.seed, args.context_size)
    samples = load_personamem(args.tier, seed=args.seed, context_size=args.context_size)
    log.info("加载完成，共 %d 道题", len(samples))

    llm = OpenAIProvider()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_dir = RESULTS_ROOT / f"{timestamp}_{args.tier}"
    output_dir.mkdir(parents=True, exist_ok=True)

    base_temp = Path(tempfile.mkdtemp(prefix="personamem_"))

    t_start = time.perf_counter()

    semaphore = asyncio.Semaphore(concurrency)
    completed = 0
    total = len(samples)
    lock = asyncio.Lock()

    async def _run_one(idx: int, sample: dict) -> dict:
        nonlocal completed
        async with semaphore:
            result = await run_one_sample(sample, llm, base_temp)
        async with lock:
            completed += 1
            correct_str = "✓" if result.get("correct") else "✗"
            log.info("[%3d/%d] %s q=%s type=%s pred=%s gold=%s raw=%s",
                     completed, total, correct_str,
                     sample.get("question_id", "")[:8],
                     sample.get("question_type", "unknown"),
                     result.get("predicted", "?"),
                     result.get("gold", "?"),
                     (result.get("raw") or "")[:60].replace("\n", " "))
        return result

    tasks = [_run_one(i, s) for i, s in enumerate(samples)]
    all_results_raw = await asyncio.gather(*tasks, return_exceptions=True)

    all_results: list[dict] = []
    errors = 0
    for i, r in enumerate(all_results_raw):
        if isinstance(r, Exception):
            log.error("样本 %d 异常: %s", i, r)
            errors += 1
            continue
        if isinstance(r, list):
            all_results.extend(r)
        else:
            all_results.append(r)

    elapsed = time.perf_counter() - t_start

    correct_count = sum(1 for q in all_results if q.get("correct"))
    log.info("完成: %d 题 / 正确 %d / 准确率 %.2f%% / 异常 %d / %d 秒",
             len(all_results), correct_count,
             correct_count / max(len(all_results), 1) * 100,
             errors, int(elapsed))

    metadata = {
        "tier": args.tier,
        "seed": args.seed,
        "concurrency": concurrency,
        "context_size": args.context_size,
        "model": _llm_cfg("model"),
        "memory_backend": "default",
        "sample_count": len(samples),
        "question_count": len(all_results),
        "answers_parsed": sum(1 for q in all_results if q.get("predicted") is not None),
        "errors": errors,
        "duration_seconds": int(elapsed),
    }

    build_report(all_results, metadata, output_dir)
    log.info("报告已产出: %s", output_dir)

    shutil.rmtree(base_temp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
