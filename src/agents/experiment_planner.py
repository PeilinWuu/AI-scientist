"""Feedback-aware experiment planner."""

from __future__ import annotations

from typing import Any

import numpy as np

from src.llm.base import LLMProvider
from src.schemas import (
    Constraints,
    ExperimentCandidate,
    ExperimentParams,
    IterationPlan,
)
from src.utils.llm_audit import LLMCallRecorder, evidence_from_raw


class ExperimentPlannerAgent:
    """Plans parameter candidates using previous measured results."""

    def plan(
        self,
        iteration: int,
        constraints: Constraints,
        problem: dict[str, Any],
        history: list[dict[str, Any]],
        llm: LLMProvider | None = None,
        recorder: LLMCallRecorder | None = None,
    ) -> IterationPlan:
        """Create the next experiment plan.

        Iteration 1 uses a small structured design. Later iterations adjust
        around the current best candidate using critic feedback and trends.
        """

        evidence = self._call_planner_llm(iteration, constraints, problem, history, llm, recorder)

        if iteration == 1 or not history:
            return self._initial_plan(iteration, problem, evidence)

        previous = history[-1]
        feedback = previous["feedback"]
        best = previous["analysis"]["best_candidate"]
        base = ExperimentParams(
            amplitude=best["amplitude"],
            frequency=best["frequency"],
            wavelength=best["wavelength"],
            stiffness=best["stiffness"],
            phase=best["phase"],
        )

        adjusted = self._apply_feedback(base, feedback, problem.get("human_feedback"))
        candidates = self._local_candidates(iteration, adjusted, constraints, feedback)
        strategy = (
            "Feedback-aware local search around the best feasible design. "
            f"Main adjustment: {feedback.get('next_strategy', 'continue local search')}."
        )
        return IterationPlan(
            iteration=iteration,
            strategy=strategy,
            candidates=candidates,
            planning_source="qwen_problem_analysis + previous_iteration_feedback",
            llm_evidence=evidence,
        )

    def _initial_plan(
        self, iteration: int, problem: dict[str, Any] | None = None, evidence: dict | None = None
    ) -> IterationPlan:
        """Build a compact first-pass experimental design."""

        preference = (problem or {}).get("planning_preference", "balanced_efficiency")
        seeds_by_preference = {
            "high_speed": [
                (0.28, 2.10, 1.05, 0.46, 0.22, "Qwen high-speed candidate"),
                (0.32, 2.45, 1.00, 0.50, 0.25, "High thrust and high frequency"),
                (0.26, 2.25, 1.20, 0.42, 0.18, "Fast wave with moderate flexibility"),
                (0.34, 1.95, 0.95, 0.55, 0.28, "Large amplitude speed exploration"),
                (0.30, 2.65, 1.10, 0.58, 0.25, "Upper-frequency speed probe"),
                (0.24, 2.35, 1.35, 0.48, 0.32, "Efficient high-speed variant"),
            ],
            "low_energy": [
                (0.14, 0.85, 1.20, 0.45, 0.20, "Qwen low-energy conservative baseline"),
                (0.16, 1.05, 1.30, 0.50, 0.25, "Low frequency efficiency probe"),
                (0.18, 1.15, 1.10, 0.42, 0.20, "Moderate amplitude low-cost gait"),
                (0.12, 1.25, 1.45, 0.55, 0.30, "Very low amplitude energy saver"),
                (0.20, 0.95, 1.00, 0.48, 0.18, "Energy-speed compromise"),
                (0.15, 1.35, 1.25, 0.60, 0.25, "Stable low-energy variant"),
            ],
            "stability_first": [
                (0.14, 1.20, 1.25, 0.68, 0.20, "Qwen stability-first baseline"),
                (0.16, 1.40, 1.30, 0.72, 0.25, "Higher stiffness stability probe"),
                (0.18, 1.10, 1.45, 0.78, 0.18, "Smooth wave stable candidate"),
                (0.12, 1.55, 1.15, 0.70, 0.28, "Low amplitude stable frequency probe"),
                (0.20, 1.30, 1.35, 0.75, 0.22, "Stable but propulsive variant"),
                (0.15, 1.70, 1.05, 0.80, 0.30, "Stiffness-heavy stability probe"),
            ],
            "balanced_efficiency": [
                (0.16, 1.05, 1.10, 0.45, 0.20, "Balanced low-risk baseline"),
                (0.24, 1.60, 1.10, 0.48, 0.25, "Higher thrust with moderate cost"),
                (0.30, 2.10, 1.00, 0.55, 0.25, "Aggressive speed-seeking candidate"),
                (0.18, 2.30, 1.25, 0.50, 0.35, "Frequency-focused candidate"),
                (0.26, 1.35, 0.85, 0.35, 0.15, "Flexible body exploration"),
                (0.20, 1.45, 1.45, 0.70, 0.30, "Stiffer swimmer exploration"),
            ],
        }
        seeds = seeds_by_preference.get(preference, seeds_by_preference["balanced_efficiency"])
        candidates = [
            ExperimentCandidate(
                candidate_id=f"iter{iteration}_cand{i + 1}",
                params=ExperimentParams(
                    amplitude=a,
                    frequency=f,
                    wavelength=w,
                    stiffness=s,
                    phase=p,
                ),
                rationale=r,
            )
            for i, (a, f, w, s, p, r) in enumerate(seeds)
        ]
        return IterationPlan(
            iteration=iteration,
            strategy=f"Qwen-guided first pass for planning_preference={preference}.",
            candidates=candidates,
            planning_source="qwen_problem_analysis + previous_iteration_feedback",
            llm_evidence=evidence,
        )

    def _apply_feedback(
        self,
        base: ExperimentParams,
        feedback: dict[str, Any],
        human_feedback: str | None,
    ) -> ExperimentParams:
        """Translate critic feedback into a new center point."""

        amp = base.amplitude
        freq = base.frequency
        wav = base.wavelength
        stiff = base.stiffness
        phase = base.phase

        if feedback.get("energy_too_high"):
            freq -= 0.22
            amp -= 0.035
        if feedback.get("stability_too_low"):
            amp -= 0.04
            stiff += 0.08
        if feedback.get("speed_too_low"):
            freq += 0.18
            amp += 0.03
        if feedback.get("efficiency_improved"):
            wav = 0.75 * wav + 0.25 * 1.10
            phase = 0.75 * phase + 0.25 * 0.25

        adjustment = feedback.get("parameter_adjustment") or {}
        freq += self._direction_delta(adjustment.get("frequency"), 0.16)
        amp += self._direction_delta(adjustment.get("amplitude"), 0.025)
        stiff += self._direction_delta(adjustment.get("stiffness"), 0.06)
        wav += self._direction_delta(adjustment.get("wavelength"), 0.06)
        phase += self._direction_delta(adjustment.get("phase"), 0.03)

        hf = (human_feedback or "").lower()
        if "low energy" in hf or "低能耗" in hf or "能耗" in hf:
            freq -= 0.12
            amp -= 0.02
        if "stability" in hf or "稳定" in hf:
            amp -= 0.025
            stiff += 0.06

        return ExperimentParams(
            amplitude=amp,
            frequency=freq,
            wavelength=wav,
            stiffness=stiff,
            phase=phase,
        )

    def _direction_delta(self, direction: str | None, magnitude: float) -> float:
        """Translate Qwen critic adjustment words to numeric deltas."""

        if direction == "increase":
            return magnitude
        if direction == "decrease":
            return -magnitude
        return 0.0

    def _call_planner_llm(
        self,
        iteration: int,
        constraints: Constraints,
        problem: dict[str, Any],
        history: list[dict[str, Any]],
        llm: LLMProvider | None,
        recorder: LLMCallRecorder | None,
    ) -> dict | None:
        """Record a planner LLM call so planning has explicit evidence."""

        if not llm:
            return None
        system_prompt = (
            "You are ExperimentPlannerAgent. Return a concise JSON object with "
            "planning_rationale and expected_tradeoff. No markdown."
        )
        user_prompt = (
            f"iteration={iteration}\n"
            f"constraints={constraints.model_dump()}\n"
            f"qwen_problem_analysis={problem.get('qwen_problem_analysis', problem)}\n"
            f"recent_history={history[-1:] if history else []}\n"
            "Explain why the next candidate plan should follow the current preference and feedback."
        )
        if recorder:
            return recorder.call("ExperimentPlannerAgent", system_prompt, user_prompt).evidence
        raw = llm.generate(system_prompt, user_prompt)
        return evidence_from_raw(llm, raw)

    def _local_candidates(
        self,
        iteration: int,
        center: ExperimentParams,
        constraints: Constraints,
        feedback: dict[str, Any],
    ) -> list[ExperimentCandidate]:
        """Generate deterministic local perturbations around the adjusted center."""

        if feedback.get("energy_too_high"):
            offsets = [
                (-0.02, -0.20, 0.00, 0.00, 0.00, "Reduce cost from frequency and amplitude"),
                (-0.04, -0.10, 0.08, 0.03, 0.00, "Lower amplitude with slightly smoother wave"),
                (0.00, -0.28, 0.00, 0.05, -0.03, "Frequency cut while preserving stability"),
                (0.02, -0.16, -0.06, 0.00, 0.03, "Recover speed after energy reduction"),
            ]
        elif feedback.get("stability_too_low"):
            offsets = [
                (-0.04, -0.08, 0.05, 0.10, 0.00, "Improve stability through lower amplitude and higher stiffness"),
                (-0.02, 0.00, 0.10, 0.08, -0.04, "Smoother wave with stabilized body"),
                (-0.06, 0.12, 0.00, 0.12, 0.02, "Trade amplitude for frequency under better stiffness"),
                (0.00, -0.12, -0.05, 0.06, 0.00, "Dampen unstable oscillation"),
            ]
        elif feedback.get("speed_too_low"):
            offsets = [
                (0.03, 0.18, 0.00, 0.00, 0.00, "Increase thrust for low speed"),
                (0.01, 0.28, -0.05, 0.03, 0.02, "Frequency increase with controlled body shape"),
                (0.05, 0.08, 0.05, 0.00, -0.02, "Amplitude increase with mild smoothing"),
                (0.00, 0.16, 0.00, -0.04, 0.04, "Use flexibility to recover speed"),
            ]
        else:
            offsets = [
                (0.00, 0.00, 0.00, 0.00, 0.00, "Re-test current best center"),
                (0.02, 0.10, -0.04, 0.02, 0.00, "Slightly faster local variant"),
                (-0.02, -0.10, 0.04, 0.04, -0.02, "Lower-cost local variant"),
                (0.00, 0.06, 0.08, -0.03, 0.03, "Flow-shape local variant"),
            ]

        candidates: list[ExperimentCandidate] = []
        for idx, (da, df, dw, ds, dp, rationale) in enumerate(offsets, start=1):
            params = ExperimentParams(
                amplitude=float(np.clip(center.amplitude + da, *constraints.amplitude_range)),
                frequency=float(np.clip(center.frequency + df, *constraints.frequency_range)),
                wavelength=float(np.clip(center.wavelength + dw, *constraints.wavelength_range)),
                stiffness=float(np.clip(center.stiffness + ds, *constraints.stiffness_range)),
                phase=float(np.clip(center.phase + dp, *constraints.phase_range)),
            )
            candidates.append(
                ExperimentCandidate(
                    candidate_id=f"iter{iteration}_cand{idx}",
                    params=params,
                    rationale=rationale,
                )
            )
        return candidates
