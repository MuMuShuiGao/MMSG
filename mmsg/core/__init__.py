import logging

from .plugin import llm_registry, tool_registry

_LEVEL_MAP: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def setup_logging(level: int | str = logging.INFO) -> None:
    """配置日志：同时输出到 stdout 和 mmsg.log 文件。"""
    if isinstance(level, str):
        level = _LEVEL_MAP.get(level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("mmsg.log", encoding="utf-8", delay=True),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


__all__ = [
    "llm_registry",
    "tool_registry",
    "setup_logging",
]
