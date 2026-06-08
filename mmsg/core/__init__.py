from .bus import Event, EventBus
from .plugin import llm_registry, memory_registry, tool_registry

import logging


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )


__all__ = [
    "Event",
    "EventBus",
    "llm_registry",
    "memory_registry",
    "tool_registry",
    "setup_logging",
]
