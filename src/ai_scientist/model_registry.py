"""Environment-driven Qwen model assignment for research roles."""

from __future__ import annotations

import os
from dataclasses import dataclass

from src.ai_scientist.exceptions import ModelConfigurationError
from src.model_utils import normalize_model_overrides


ROLE_RESEARCH_DIRECTOR = "research_director"
ROLE_EVIDENCE_RESEARCHER = "evidence_researcher"
ROLE_METHODOLOGIST = "methodologist"
ROLE_HYPOTHESIS_SCIENTIST = "hypothesis_scientist"
ROLE_STUDY_DESIGNER = "study_designer"
ROLE_ANALYST = "analyst"
ROLE_REPRODUCIBILITY_ENGINEER = "reproducibility_engineer"
ROLE_SKEPTICAL_REVIEWER = "skeptical_reviewer"
ROLE_SCIENTIFIC_SYNTHESIZER = "scientific_synthesizer"

ROLE_ENV_VARS = {
    ROLE_RESEARCH_DIRECTOR: "AI_SCIENTIST_DIRECTOR_MODEL",
    ROLE_EVIDENCE_RESEARCHER: "AI_SCIENTIST_RESEARCH_MODEL",
    ROLE_METHODOLOGIST: "AI_SCIENTIST_METHODOLOGIST_MODEL",
    ROLE_HYPOTHESIS_SCIENTIST: "AI_SCIENTIST_HYPOTHESIS_MODEL",
    ROLE_STUDY_DESIGNER: "AI_SCIENTIST_DESIGNER_MODEL",
    ROLE_ANALYST: "AI_SCIENTIST_ANALYST_MODEL",
    ROLE_REPRODUCIBILITY_ENGINEER: "AI_SCIENTIST_REPRODUCIBILITY_MODEL",
    ROLE_SKEPTICAL_REVIEWER: "AI_SCIENTIST_REVIEWER_MODEL",
    ROLE_SCIENTIFIC_SYNTHESIZER: "AI_SCIENTIST_SYNTHESIZER_MODEL",
}


@dataclass(frozen=True)
class ModelResolution:
    agent_name: str
    role: str
    override_model: str | None
    environment_model: str | None
    requested_model: str
    actual_model: str
    fallback_model: str
    resolved_model: str
    resolution_source: str
    fallback_used: bool


class ModelRegistry:
    """Resolve role models without hard-coding model IDs in agents."""

    def __init__(self, overrides: dict[str, str | None] | None = None) -> None:
        self.overrides = normalize_model_overrides(overrides)
        self.fallback_model = (
            self.overrides.get("fallback")
            or os.getenv("AI_SCIENTIST_FALLBACK_MODEL", "").strip()
            or os.getenv("LLM_MODEL", "").strip()
            or "qwen-turbo"
        )

    def resolve(self, agent_name: str) -> ModelResolution:
        return self.resolve_model(agent_name)

    def resolve_model(self, role: str) -> ModelResolution:
        """Resolve one role model using project overrides before environment defaults."""

        agent_name = role
        if agent_name not in ROLE_ENV_VARS:
            raise KeyError(f"Unknown AI Scientist role: {agent_name}")
        override_model = self.overrides.get(agent_name)
        environment_model = os.getenv(ROLE_ENV_VARS[agent_name], "").strip() or None
        fallback = self.overrides.get("fallback") or os.getenv("AI_SCIENTIST_FALLBACK_MODEL", "").strip() or None
        search_default = os.getenv("LLM_SEARCH_MODEL", "").strip() or None
        llm_default = os.getenv("LLM_MODEL", "").strip() or None
        if override_model:
            selected = override_model
            source = "override"
        elif environment_model:
            selected = environment_model
            source = "environment"
        elif fallback:
            selected = fallback
            source = "fallback"
        elif agent_name == ROLE_EVIDENCE_RESEARCHER and search_default:
            selected = search_default
            source = "search_default"
        elif llm_default:
            selected = llm_default
            source = "llm_default"
        else:
            raise ModelConfigurationError(f"No valid model is configured for {agent_name}.")
        return ModelResolution(
            agent_name=agent_name,
            role=agent_name,
            override_model=override_model,
            environment_model=environment_model,
            requested_model=selected,
            actual_model=selected,
            fallback_model=fallback or search_default or llm_default or self.fallback_model,
            resolved_model=selected,
            resolution_source=source,
            fallback_used=source != "override" and source != "environment",
        )

    def public_configuration(self) -> dict[str, str]:
        return {
            role: self.resolve_model(role).resolved_model
            for role in ROLE_ENV_VARS
        }

    @classmethod
    def public_defaults(cls) -> dict[str, object]:
        registry = cls()
        return {
            "roles": registry.public_configuration(),
            "fallback_model": registry.fallback_model,
        }
