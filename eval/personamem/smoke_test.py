"""Smoke test for PersonaMem eval pipeline."""
import asyncio
import logging
import tempfile
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("smoke")

from eval.personamem.dataset import load_personamem
from eval.personamem.runner import run_one_sample
from mmsg.core import llm_registry
from mmsg.llm import OpenAIProvider

llm_registry.register("openai")(OpenAIProvider)


async def test():
    log.info("加载数据集...")
    samples = load_personamem("smoke", seed=42)
    s = samples[0]
    log.info("ID: %s, type: %s, turns: %d", s["question_id"], s["question_type"], len(s["history_turns"]))

    llm = OpenAIProvider()
    base = Path(tempfile.mkdtemp(prefix="smoke_"))

    t0 = time.perf_counter()
    r = await run_one_sample(s, llm, base)
    elapsed = time.perf_counter() - t0

    log.info("correct=%s, pred=%s, gold=%s, elapsed=%.1fs",
             r["correct"], r["predicted"], r["gold"], elapsed)
    log.info("raw first 300: %s", r["raw"][:300] if r["raw"] else "EMPTY")

    log.info("workspace 保留在: %s", base)


if __name__ == "__main__":
    asyncio.run(test())
