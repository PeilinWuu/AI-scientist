"""Tests for feedback-aware experiment planning."""

from __future__ import annotations

from src.agents.experiment_planner import ExperimentPlannerAgent
from src.schemas import Constraints


def test_energy_feedback_lowers_frequency_or_amplitude() -> None:
    best = _best_candidate(energy_cost=2.4, frequency=2.2, amplitude=0.3)
    history = [_history_item(best, {"energy_too_high": True})]

    plan = ExperimentPlannerAgent().plan(2, Constraints(), {}, history)

    assert any(
        candidate.params.frequency < best["frequency"]
        or candidate.params.amplitude < best["amplitude"]
        for candidate in plan.candidates
    )


def test_stability_feedback_lowers_amplitude_or_raises_stiffness() -> None:
    best = _best_candidate(stability_score=0.55, amplitude=0.34, stiffness=0.42)
    history = [_history_item(best, {"stability_too_low": True})]

    plan = ExperimentPlannerAgent().plan(2, Constraints(), {}, history)

    assert any(
        candidate.params.amplitude < best["amplitude"]
        or candidate.params.stiffness > best["stiffness"]
        for candidate in plan.candidates
    )


def test_speed_feedback_raises_frequency_or_amplitude() -> None:
    best = _best_candidate(mean_speed=0.4, frequency=1.1, amplitude=0.15)
    history = [_history_item(best, {"speed_too_low": True})]

    plan = ExperimentPlannerAgent().plan(2, Constraints(), {}, history)

    assert any(
        candidate.params.frequency > best["frequency"]
        or candidate.params.amplitude > best["amplitude"]
        for candidate in plan.candidates
    )


def test_improved_target_metric_uses_local_search_around_best_candidate() -> None:
    best = _best_candidate(
        efficiency=2.2,
        amplitude=0.22,
        frequency=1.5,
        wavelength=1.1,
        stiffness=0.5,
        phase=0.25,
    )
    history = [_history_item(best, {"efficiency_improved": True})]

    plan = ExperimentPlannerAgent().plan(2, Constraints(), {}, history)

    assert "local search" in plan.strategy
    assert any(
        abs(candidate.params.amplitude - best["amplitude"]) <= 0.02
        and abs(candidate.params.frequency - best["frequency"]) <= 0.10
        for candidate in plan.candidates
    )


def _best_candidate(**overrides) -> dict:
    best = {
        "candidate_id": "iter1_cand1",
        "amplitude": 0.24,
        "frequency": 1.6,
        "wavelength": 1.1,
        "stiffness": 0.48,
        "phase": 0.25,
        "mean_speed": 1.0,
        "energy_cost": 1.0,
        "efficiency": 1.5,
        "stability_score": 0.8,
        "vortex_loss": 0.1,
        "constraint_violation": False,
    }
    best.update(overrides)
    return best


def _history_item(best: dict, feedback: dict) -> dict:
    full_feedback = {
        "energy_too_high": False,
        "stability_too_low": False,
        "speed_too_low": False,
        "efficiency_improved": False,
        "next_strategy": "test feedback rule",
    }
    full_feedback.update(feedback)
    return {
        "iteration": 1,
        "analysis": {"best_candidate": best},
        "feedback": full_feedback,
    }
