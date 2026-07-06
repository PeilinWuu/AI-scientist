"""LLM provider interface used by agents."""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Minimal abstraction so Mock and Qwen can be swapped cleanly."""

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Return a text completion for an agent prompt."""

    def metadata(self) -> dict:
        """Return safe provider metadata for run audit logs."""

        return {
            "provider": "unknown",
            "transport": "unknown",
            "model": "unknown",
            "base_url": "",
            "is_mock": True,
        }
