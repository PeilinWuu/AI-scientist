"""Environment-driven Qwen model assignment for research roles."""

from __future__ import annotations

import os
from dataclasses import dataclass

from src.model_utils import normalize_model_overrides


ROLE_ENV_VARS = {
    "research_director": "AI_SCIENTIST_DIRECTOR_MODEL",
    "evidence_researcher": "AI_SCIENTIST_RESEARCH_MODEL",
    "methodologist": "AI_SCIENTIST_METHODOLOGIST_MODEL",
    "hypothesis_scientist": "AI_SCIENTIST_HYPOTHESIS_MODEL",
    "study_designer": "AI_SCIENTIST_DESIGNER_MODEL",
    "analyst": "AI_SCIENTIST_ANALYST_MODEL",
    "reproducibility_engineer": "AI_SCIENTIST_REPRODUCIBILITY_MODEL",
    "skeptical_reviewer": "AI_SCIENTIST_REVIEWER_MODEL",
    "scientific_synthesizer": "AI_SCIENTIST_SYNTHESIZER_MODEL",
}


@dataclass(frozen=True)
class ModelResolution:
    agent_name: str
    requested_model: str
    actual_model: str
    fallback_model: str
    fallback_used: bool


class ModelRegistry:
    """Resolve role models without hard-coding model IDs in agents."""

    def __init__(self, overrides: dict[str, str | None] | None = None) -> None:
        self.overrides = normalize_model_overrides(overrides)
        self.fallback_model = self.overrides.get("fallback") or os.getenv("AI_SCIENTIST_FALLBACK_MODEL") or os.getenv(
            "LLM_MODEL", "qwen-turbo"
        )

    def resolve(self, agent_name: str) -> ModelResolution:
        if agent_name not in ROLE_ENV_VARS:
            raise KeyError(f"Unknown AI Scientist role: {agent_name}")
        configured = self.overrides.get(agent_name) or os.getenv(ROLE_ENV_VARS[agent_name], "").strip()
        selected = configured or self.fallback_model
        return ModelResolution(
            agent_name=agent_name,
            requested_model=selected,
            actual_model=selected,
            fallback_model=self.fallback_model,
            fallback_used=not bool(configured),
        )

    def public_configuration(self) -> dict[str, str]:
        return {
            role: (os.getenv(env_name, "").strip() or self.fallback_model)
            for role, env_name in ROLE_ENV_VARS.items()
        }

    @classmethod
    def public_defaults(cls) -> dict[str, object]:
        registry = cls()
        return {
            "roles": registry.public_configuration(),
            "fallback_model": registry.fallback_model,
        }
