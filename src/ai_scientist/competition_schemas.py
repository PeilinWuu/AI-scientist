"""Strict, auditable schemas for the Competition 1B execution loop."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def competition_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


ExecutionOperation = Literal[
    "inspect_dataset", "describe_dataset", "missingness", "correlation",
    "linear_regression", "grouped_summary", "frequency_table", "contingency_table",
    "time_series_summary", "text_summary", "permutation_group_comparison",
    "plot_histogram", "plot_scatter", "run_simulation",
]


class CompetitionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutionRequest(CompetitionModel):
    request_id: str = Field(default_factory=lambda: competition_id("request"))
    operation: ExecutionOperation
    inputs: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_outputs: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    seed: int = Field(default=0, ge=0, le=2_147_483_647)
    output_directory: str = "execution"

    @model_validator(mode="after")
    def reject_unsafe_output_directory(self) -> "ExecutionRequest":
        value = self.output_directory.replace("\\", "/")
        if value.startswith("/") or ":" in value or ".." in value.split("/"):
            raise ValueError("output_directory must be a project-relative safe path")
        return self


class ExecutionArtifact(CompetitionModel):
    artifact_id: str = Field(default_factory=lambda: competition_id("artifact"))
    artifact_type: str
    relative_path: str
    media_type: str
    checksum_sha256: str
    size_bytes: int = Field(ge=0)


class ExecutionResult(CompetitionModel):
    execution_id: str = Field(default_factory=lambda: competition_id("execution"))
    request_id: str
    operation: str
    status: Literal["success", "failed", "rejected"]
    started_at: datetime
    finished_at: datetime
    duration_ms: int = Field(ge=0)
    seed: int
    input_fingerprint: str
    input_checksums: dict[str, str] = Field(default_factory=dict)
    actual_parameters: dict[str, Any] = Field(default_factory=dict)
    software_versions: dict[str, str] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ExecutionArtifact] = Field(default_factory=list)
    run_log: list[str] = Field(default_factory=list)
    failure_reason: str | None = None


class FeedbackSignal(CompetitionModel):
    signal_id: str = Field(default_factory=lambda: competition_id("feedback"))
    source_artifact_ids: list[str]
    observed_result: dict[str, Any]
    expected_result: dict[str, Any] | None = None
    quality_flags: list[str] = Field(default_factory=list)
    trigger: str
    confidence: float | None = Field(default=None, ge=0, le=1)


AdjustmentDecision = Literal[
    "keep", "modify", "add", "remove", "reorder", "stop", "human_review"
]


class PlanAdjustment(CompetitionModel):
    adjustment_id: str = Field(default_factory=lambda: competition_id("adjustment"))
    target_task_id: str
    field: str
    old_value: Any
    new_value: Any
    reason: str
    evidence_refs: list[str]
    decision: AdjustmentDecision


class IterationRecord(CompetitionModel):
    iteration: int = Field(ge=1)
    plan_version: str
    execution_ids: list[str]
    analysis_artifact_ids: list[str]
    feedback_signals: list[FeedbackSignal] = Field(default_factory=list)
    adjustments: list[PlanAdjustment] = Field(default_factory=list)
    next_plan_version: str | None = None
    decision: Literal["continue", "stop", "human_review"] = "continue"
    created_at: datetime = Field(default_factory=now_utc)


class DampedOscillatorPlan(CompetitionModel):
    plan_version: str
    task_id: str = "fit_damped_oscillator"
    damping_min: float = Field(ge=0.001, le=5)
    damping_max: float = Field(ge=0.001, le=5)
    damping_points: int = Field(ge=3, le=500)
    omega_min: float = Field(gt=0, le=100)
    omega_max: float = Field(gt=0, le=100)
    omega_points: int = Field(ge=3, le=500)
    success_rmse: float = Field(gt=0, le=10)
    resource_budget_evaluations: int = Field(ge=9, le=250_000)
    derived_from_execution_id: str | None = None
    rationale: str

    @model_validator(mode="after")
    def validate_ranges_and_budget(self) -> "DampedOscillatorPlan":
        if self.damping_min >= self.damping_max:
            raise ValueError("damping_min must be lower than damping_max")
        if self.omega_min >= self.omega_max:
            raise ValueError("omega_min must be lower than omega_max")
        if self.damping_points * self.omega_points != self.resource_budget_evaluations:
            raise ValueError("resource budget must equal damping_points * omega_points")
        return self


class CompetitionRunState(CompetitionModel):
    run_id: str
    case_name: str = "damped_oscillator_parameter_identification"
    status: Literal["created", "round_1_complete", "complete", "failed", "human_review"]
    seed: int
    root_directory: str
    plans: list[DampedOscillatorPlan] = Field(default_factory=list)
    executions: list[ExecutionResult] = Field(default_factory=list)
    iterations: list[IterationRecord] = Field(default_factory=list)
    baseline_execution: ExecutionResult | None = None
    comparison: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
