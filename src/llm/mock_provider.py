"""Deterministic mock LLM provider for offline demos."""

from __future__ import annotations

from src.config import settings

from .base import LLMProvider


class MockLLMProvider(LLMProvider):
    """Returns compact, deterministic text so the project runs without API keys."""

    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        return (
            "MockLLM: use constraint-aware local search, compare each iteration "
            "against previous results, and update the next plan from measured failures."
        )

    def metadata(self) -> dict:
        return {
            "provider": "mock",
            "transport": "none",
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "is_mock": True,
        }
