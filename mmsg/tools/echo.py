"""Demo tools so the loop has something to call."""
from __future__ import annotations

import datetime as dt
from typing import Any

from .base import Tool


class EchoTool(Tool):
    name = "echo"
    description = "Echo input text back verbatim. Use for testing."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "text to echo"}},
        "required": ["text"],
    }

    async def run(self, **kwargs: Any) -> str:
        return str(kwargs.get("text", ""))


class NowTool(Tool):
    name = "now"
    description = "Return current ISO-8601 local datetime."
    parameters = {"type": "object", "properties": {}}

    async def run(self, **kwargs: Any) -> str:
        return dt.datetime.now().isoformat(timespec="seconds")
