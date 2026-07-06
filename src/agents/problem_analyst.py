"""Problem analysis agent."""

from __future__ import annotations

from src.config import settings
from src.llm.base import LLMProvider
from src.schemas import Constraints
from src.utils.llm_audit import LLMCallRecorder, evidence_from_raw, parse_llm_json


class ProblemAnalystAgent:
    """Extracts variables, constraints, and Qwen-derived planning priorities."""

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    def analyze(
        self,
        research_goal: str,
        constraints: Constraints,
        human_feedback: str | None,
        recorder: LLMCallRecorder | None = None,
    ) -> dict:
        """Ask Qwen for strict JSON problem analysis and parse it."""

        system_prompt = (
            "You are ProblemAnalystAgent for a closed-loop scientific experiment "
            "planner. Return strict JSON only. No markdown."
        )
        user_prompt = f"""
Analyze this soft robotic swimmer experiment goal and constraints.

research_goal: {research_goal}
human_feedback: {human_feedback or "None"}
constraints: {constraints.model_dump()}

Return exactly this JSON schema:
{{
  "problem_statement": "...",
  "target_metric": "efficiency|mean_speed|energy_cost|stability_score",
  "priority_weights": {{
    "mean_speed": 0.0,
    "energy_cost": 0.0,
    "efficiency": 0.0,
    "stability_score": 0.0
  }},
  "constraints_interpretation": "...",
  "planning_preference": "high_speed|low_energy|stability_first|balanced_efficiency"
}}
"""

        try:
            if recorder:
                record = recorder.call("ProblemAnalystAgent", system_prompt, user_prompt)
                raw_response = record.raw_response
                evidence = record.evidence
            else:
                raw_response = self.llm.generate(system_prompt, user_prompt)
                evidence = evidence_from_raw(self.llm, raw_response)
            qwen_analysis = parse_llm_json(raw_response, "ProblemAnalystAgent")
        except Exception as exc:  # noqa: BLE001 - workflow should remain runnable offline.
            if settings.qwen_require_real:
                metadata = self.llm.metadata()
                raise RuntimeError(
                    "Real Qwen is required. Mock fallback is disabled. "
                    f"Provider={metadata.get('provider', 'unknown')}; "
                    f"Transport={metadata.get('transport', 'unknown')}; "
                    f"Model={metadata.get('model', 'unknown')}; "
                    f"Base URL={metadata.get('base_url', 'unknown')}; "
                    f"Original error type={type(exc).__name__}; "
                    f"Original error={_sanitize_error(str(exc))}"
                ) from exc
            raw_response = "Development fallback problem analysis."
            qwen_analysis = self._heuristic_analysis(
                research_goal, constraints, human_feedback
            )
            evidence = evidence_from_raw(self.llm, raw_response)

        qwen_analysis = self._normalize_analysis(qwen_analysis, constraints)
        if settings.qwen_require_real and not evidence.get("llm_used"):
            raise RuntimeError("ProblemAnalystAgent did not use a real LLM response.")

        return {
            "research_goal": research_goal,
            "problem_statement": qwen_analysis["problem_statement"],
            "target_metric": qwen_analysis["target_metric"],
            "priority_weights": qwen_analysis["priority_weights"],
            "constraints_interpretation": qwen_analysis["constraints_interpretation"],
            "planning_preference": qwen_analysis["planning_preference"],
            "variables": [
                "amplitude",
                "frequency",
                "wavelength",
                "stiffness",
                "phase",
            ],
            "constraints": constraints.model_dump(),
            "priorities": self._priorities_from_weights(qwen_analysis["priority_weights"]),
            "human_feedback": human_feedback,
            "llm_evidence": evidence,
            "qwen_problem_analysis": qwen_analysis,
        }

    def _normalize_analysis(self, data: dict, constraints: Constraints) -> dict:
        """Validate and normalize Qwen problem analysis."""

        target = data.get("target_metric", constraints.target_metric)
        if target not in {"efficiency", "mean_speed", "energy_cost", "stability_score"}:
            target = constraints.target_metric
        weights = data.get("priority_weights") or {}
        normalized_weights = {
            "mean_speed": float(weights.get("mean_speed", 0.25)),
            "energy_cost": float(weights.get("energy_cost", 0.25)),
            "efficiency": float(weights.get("efficiency", 0.25)),
            "stability_score": float(weights.get("stability_score", 0.25)),
        }
        preference = str(data.get("planning_preference", "balanced_efficiency"))
        preference_map = {
            "explore_high_speed": "high_speed",
            "conservative_low_energy": "low_energy",
            "high_speed": "high_speed",
            "low_energy": "low_energy",
            "stability_first": "stability_first",
            "balanced_efficiency": "balanced_efficiency",
        }
        return {
            "problem_statement": str(data.get("problem_statement", "")),
            "target_metric": target,
            "priority_weights": normalized_weights,
            "constraints_interpretation": str(data.get("constraints_interpretation", "")),
            "planning_preference": preference_map.get(preference, "balanced_efficiency"),
        }

    def _heuristic_analysis(
        self, research_goal: str, constraints: Constraints, human_feedback: str | None
    ) -> dict:
        """Development-only fallback when real Qwen is not required."""

        text = f"{research_goal} {human_feedback or ''}".lower()
        if "speed" in text or "fast" in text:
            preference = "high_speed"
            target = "mean_speed"
            weights = {
                "mean_speed": 0.45,
                "energy_cost": 0.15,
                "efficiency": 0.25,
                "stability_score": 0.15,
            }
        elif "energy" in text or "cost" in text or "能耗" in text:
            preference = "low_energy"
            target = "energy_cost"
            weights = {
                "mean_speed": 0.15,
                "energy_cost": 0.45,
                "efficiency": 0.2,
                "stability_score": 0.2,
            }
        elif "stable" in text or "stability" in text or "稳定" in text:
            preference = "stability_first"
            target = "stability_score"
            weights = {
                "mean_speed": 0.15,
                "energy_cost": 0.2,
                "efficiency": 0.2,
                "stability_score": 0.45,
            }
        else:
            preference = "balanced_efficiency"
            target = constraints.target_metric
            weights = {
                "mean_speed": 0.25,
                "energy_cost": 0.2,
                "efficiency": 0.4,
                "stability_score": 0.15,
            }
        return {
            "problem_statement": research_goal,
            "target_metric": target,
            "priority_weights": weights,
            "constraints_interpretation": "Development fallback interpretation.",
            "planning_preference": preference,
        }

    def _priorities_from_weights(self, weights: dict[str, float]) -> list[str]:
        """Convert Qwen weights into ordered priority names."""

        return [
            item[0]
            for item in sorted(weights.items(), key=lambda item: item[1], reverse=True)
        ]


def _sanitize_error(message: str) -> str:
    """Avoid leaking a configured API key in raised diagnostics."""

    api_key = settings.dashscope_api_key
    return message.replace(api_key, "[REDACTED_API_KEY]") if api_key else message
