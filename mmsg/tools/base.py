"""Tool plugin contract. Tool exposes JSON-schema params + async run."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class Tool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    risk: ClassVar[str] = "safe"  # safe | write | network

    @abstractmethod
    async def run(self, **kwargs: Any) -> Any: ...

    def schema(self) -> dict[str, Any]:
        """OpenAI function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
