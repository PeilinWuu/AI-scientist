"""AI Scientist-specific exceptions."""

from __future__ import annotations

from typing import Any


class AIScientistError(RuntimeError):
    """Base error for research workflow failures."""

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        substep: str | None = None,
        cause_type: str | None = None,
        cause_message: str | None = None,
        validation_errors: list[dict[str, Any]] | None = None,
        artifact_type: str | None = None,
        failure_category: str | None = None,
        failing_component: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.substep = substep
        self.cause_type = cause_type
        self.cause_message = cause_message
        self.validation_errors = validation_errors or []
        self.artifact_type = artifact_type
        self.failure_category = failure_category
        self.failing_component = failing_component or substep


class ProjectNotFoundError(AIScientistError):
    """Raised when a persisted project does not exist."""


class InvalidTransitionError(AIScientistError):
    """Raised when a state-machine transition is not allowed."""


class InvalidReviewDecisionError(AIScientistError):
    """Raised when reviewer output is not a supported review decision."""


class SkillValidationError(AIScientistError):
    """Raised when a skill file is missing or invalid."""


class StructuredOutputError(AIScientistError):
    """Raised after structured model output cannot be validated."""


class BudgetExceededError(AIScientistError):
    """Raised when the configured research budget is exhausted."""


class ModelConfigurationError(AIScientistError):
    """Raised before provider calls when no valid model can be resolved."""


class InvalidEvidenceReferenceError(AIScientistError):
    """Raised when a claim points to evidence that is not in the active collection."""

    def __init__(self, claim_id: str, evidence_id: str) -> None:
        self.claim_id = claim_id
        self.evidence_id = evidence_id
        super().__init__(
            f"Claim {claim_id} references unknown evidence {evidence_id}.",
            stage="CLAIM_EVIDENCE_MAPPING",
            substep="evidence_reference_validation",
            cause_type="InvalidEvidenceReferenceError",
            cause_message=f"Unknown evidence_id: {evidence_id}",
            artifact_type="claim_evidence_mapping",
            failure_category="orchestration_postprocess_error",
            failing_component="evidence_reference_validation",
        )
