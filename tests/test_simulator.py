"""Tests for the lightweight soft-swimmer simulator."""

from __future__ import annotations

from src.schemas import Constraints, ExperimentParams
from src.simulator.soft_swimmer_simulator import SoftSwimmerSimulator


def test_simulator_outputs_required_metrics() -> None:
    simulator = SoftSwimmerSimulator(Constraints(), random_seed=123)
    result = simulator.evaluate(
        "cand1",
        ExperimentParams(
            amplitude=0.2,
            frequency=1.4,
            wavelength=1.1,
            stiffness=0.45,
            phase=0.25,
        ),
        iteration=1,
    ).model_dump()

    assert {
        "mean_speed",
        "energy_cost",
        "efficiency",
        "stability_score",
        "vortex_loss",
        "constraint_violation",
    }.issubset(result)


def test_simulator_is_reproducible_with_same_seed() -> None:
    constraints = Constraints()
    params = ExperimentParams(
        amplitude=0.25,
        frequency=1.8,
        wavelength=1.05,
        stiffness=0.5,
        phase=0.2,
    )
    result_a = SoftSwimmerSimulator(constraints, random_seed=77).evaluate(
        "cand1", params, iteration=1
    )
    result_b = SoftSwimmerSimulator(constraints, random_seed=77).evaluate(
        "cand1", params, iteration=1
    )

    assert result_a == result_b


def test_energy_constraint_violation_when_cost_exceeds_limit() -> None:
    constraints = Constraints(max_energy_cost=0.01, min_stability=0.0)
    params = ExperimentParams(
        amplitude=0.3,
        frequency=2.5,
        wavelength=1.0,
        stiffness=0.5,
        phase=0.25,
    )
    result = SoftSwimmerSimulator(constraints, random_seed=1).evaluate(
        "energy_fail", params, iteration=1
    )

    assert result.energy_cost > constraints.max_energy_cost
    assert result.constraint_violation is True


def test_stability_constraint_violation_when_score_below_limit() -> None:
    constraints = Constraints(max_energy_cost=999.0, min_stability=0.95)
    params = ExperimentParams(
        amplitude=0.5,
        frequency=3.0,
        wavelength=1.0,
        stiffness=0.1,
        phase=0.25,
    )
    result = SoftSwimmerSimulator(constraints, random_seed=2).evaluate(
        "stability_fail", params, iteration=1
    )

    assert result.stability_score < constraints.min_stability
    assert result.constraint_violation is True
