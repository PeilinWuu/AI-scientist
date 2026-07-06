"""Tool interface used by DialogueOrchestrator."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """Base tool contract."""

    name: str
    description: str
    schema: dict[str, Any]

    @abstractmethod
    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute the tool and return a JSON-serializable result."""
