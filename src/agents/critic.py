"""Critic agent that turns analysis into next-step guidance."""

from __future__ import annotations

from src.config import settings
from src.llm.base import LLMProvider
from src.schemas import Constraints
from src.utils.llm_audit import LLMCallRecorder, evidence_from_raw, parse_llm_json


class CriticAgent:
    """Applies simple scientific feedback rules for the next iteration."""

    def critique(
        self,
        analysis: dict,
        constraints: Constraints,
        previous_best: dict | None,
        research_goal: str | None = None,
        problem: dict | None = None,
        iteration_results: list[dict] | None = None,
        history: list[dict] | None = None,
        llm: LLMProvider | None = None,
        recorder: LLMCallRecorder | None = None,
    ) -> dict:
        """Decide which direction the next planner should move."""

        best = analysis["best_candidate"]
        energy_too_high = best["energy_cost"] > constraints.max_energy_cost * 0.95
        stability_too_low = best["stability_score"] < constraints.min_stability + 0.05
        speed_too_low = best["mean_speed"] < 0.75

        improvement = analysis.get("improvement_vs_previous_best")
        efficiency_improved = True
        if previous_best and improvement is not None:
            if constraints.target_metric == "energy_cost":
                efficiency_improved = improvement > 0
            else:
                efficiency_improved = improvement >= 0

        if energy_too_high:
            next_strategy = "reduce frequency or amplitude to lower energy cost"
        elif stability_too_low:
            next_strategy = "lower amplitude or increase stiffness to improve stability"
        elif speed_too_low:
            next_strategy = "increase frequency or amplitude to recover propulsion speed"
        elif efficiency_improved:
            next_strategy = "continue local search around the best measured candidate"
        else:
            next_strategy = "broaden local search because the last move did not improve the target"

        qwen_feedback = {}
        evidence = None
        if llm:
            qwen_feedback, evidence = self._call_critic_llm(
                llm,
                recorder,
                research_goal or "",
                problem or {},
                iteration_results or [],
                analysis,
                history or [],
            )
            if qwen_feedback.get("next_round_strategy"):
                next_strategy = qwen_feedback["next_round_strategy"]

        return {
            "energy_too_high": bool(energy_too_high),
            "stability_too_low": bool(stability_too_low),
            "speed_too_low": bool(speed_too_low),
            "efficiency_improved": bool(efficiency_improved),
            "next_strategy": next_strategy,
            "diagnosis": qwen_feedback.get("diagnosis", ""),
            "reasoning": qwen_feedback.get("reasoning", ""),
            "parameter_adjustment": qwen_feedback.get(
                "parameter_adjustment",
                {
                    "frequency": "decrease" if energy_too_high else "increase" if speed_too_low else "keep",
                    "amplitude": "decrease" if energy_too_high or stability_too_low else "increase" if speed_too_low else "keep",
                    "stiffness": "increase" if stability_too_low else "keep",
                    "wavelength": "keep",
                    "phase": "keep",
                },
            ),
            "llm_evidence": evidence,
            "message": (
                f"Next planner should {next_strategy}. "
                "This links measured results directly to the next experiment plan."
            ),
        }

    def _call_critic_llm(
        self,
        llm: LLMProvider,
        recorder: LLMCallRecorder | None,
        research_goal: str,
        problem: dict,
        iteration_results: list[dict],
        analysis: dict,
        history: list[dict],
    ) -> tuple[dict, dict]:
        """Ask Qwen to critique the iteration and return strict JSON."""

        system_prompt = (
            "You are CriticAgent for closed-loop scientific experiment planning. "
            "Return strict JSON only. No markdown."
        )
        user_prompt = f"""
research_goal: {research_goal}
qwen_problem_analysis: {problem.get('qwen_problem_analysis', problem)}
iteration_results: {iteration_results}
best_candidate: {analysis.get('best_candidate')}
failure_cases: {analysis.get('failure_reasons')}
previous_iteration_history: {history}

Return exactly:
{{
  "diagnosis": "...",
  "next_round_strategy": "...",
  "parameter_adjustment": {{
    "frequency": "increase|decrease|keep",
    "amplitude": "increase|decrease|keep",
    "stiffness": "increase|decrease|keep",
    "wavelength": "increase|decrease|keep",
    "phase": "increase|decrease|keep"
  }},
  "reasoning": "..."
}}
"""
        try:
            if recorder:
                record = recorder.call("CriticAgent", system_prompt, user_prompt)
                raw = record.raw_response
                evidence = record.evidence
            else:
                raw = llm.generate(system_prompt, user_prompt)
                evidence = evidence_from_raw(llm, raw)
            data = parse_llm_json(raw, "CriticAgent")
            data["parameter_adjustment"] = self._normalize_adjustment(
                data.get("parameter_adjustment") or {}
            )
            return data, evidence
        except Exception:
            if settings.qwen_require_real:
                raise
            return {}, evidence_from_raw(llm, "")

    def _normalize_adjustment(self, adjustment: dict) -> dict:
        """Normalize Qwen adjustment directions."""

        allowed = {"increase", "decrease", "keep"}
        return {
            key: value if value in allowed else "keep"
            for key, value in {
                "frequency": adjustment.get("frequency", "keep"),
                "amplitude": adjustment.get("amplitude", "keep"),
                "stiffness": adjustment.get("stiffness", "keep"),
                "wavelength": adjustment.get("wavelength", "keep"),
                "phase": adjustment.get("phase", "keep"),
            }.items()
        }
