from .plugin import llm_registry, tool_registry

import logging


def setup_logging(level: int = logging.INFO) -> None:
    """配置日志：同时输出到 stdout 和 mmsg.log 文件。"""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("mmsg.log", encoding="utf-8", delay=True),
        ],
    )


__all__ = [
    "llm_registry",
    "tool_registry",
    "setup_logging",
]
