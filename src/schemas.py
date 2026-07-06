"""Shared Pydantic models for API, workflow, and UI."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Constraints(BaseModel):
    """User-facing experimental constraints and optimization target."""

    min_stability: float = Field(0.65, ge=0.0, le=1.0)
    max_energy_cost: float = Field(2.2, gt=0.0)
    target_metric: Literal["efficiency", "mean_speed", "energy_cost", "stability_score"] = (
        "efficiency"
    )
    amplitude_range: tuple[float, float] = (0.05, 0.50)
    frequency_range: tuple[float, float] = (0.5, 3.0)
    wavelength_range: tuple[float, float] = (0.6, 2.0)
    stiffness_range: tuple[float, float] = (0.1, 1.0)
    phase_range: tuple[float, float] = (0.0, 1.0)


class RunRequest(BaseModel):
    research_goal: str = Field(
        "Improve soft swimmer propulsion efficiency while limiting energy cost and unstable oscillation."
    )
    constraints: Constraints = Field(default_factory=Constraints)
    max_iterations: int = Field(3, ge=1, le=10)
    human_feedback: str | None = None
    random_seed: int = 42


class HumanFeedbackRequest(BaseModel):
    human_feedback: str


class ExperimentParams(BaseModel):
    amplitude: float
    frequency: float
    wavelength: float
    stiffness: float
    phase: float


class ExperimentCandidate(BaseModel):
    candidate_id: str
    params: ExperimentParams
    rationale: str


class IterationPlan(BaseModel):
    iteration: int
    strategy: str
    candidates: list[ExperimentCandidate]
    planning_source: str | None = None
    llm_evidence: dict[str, Any] | None = None


class SimulationResult(BaseModel):
    candidate_id: str
    amplitude: float
    frequency: float
    wavelength: float
    stiffness: float
    phase: float
    mean_speed: float
    energy_cost: float
    efficiency: float
    stability_score: float
    vortex_loss: float
    constraint_violation: bool


class RunSummary(BaseModel):
    run_id: str
    best_candidate: dict[str, Any] | None = None
    best_iteration: int | None = None
    final_report_path: str | None = None
    message: str


class RunResponse(BaseModel):
    run_id: str
    summary: RunSummary
