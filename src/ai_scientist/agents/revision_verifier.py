"""Independent structured verification for a generated revision artifact."""

from __future__ import annotations

from typing import Any

from src.ai_scientist.agents.base_agent import AgentRun
from src.ai_scientist.schemas import (
    ResearchProject,
    RevisionCriterionResult,
    RevisionIssue,
    RevisionTargetBatch,
    RevisionVerificationResult,
    new_id,
    utc_now,
)
from src.ai_scientist.structured_client import StructuredQwenClient


class RevisionVerifierAgent:
    """Use an isolated verifier role; the artifact author cannot self-approve."""

    agent_name = "revision_verifier"

    def __init__(self, client: StructuredQwenClient | None = None) -> None:
        self.client = client or StructuredQwenClient()

    def verify(
        self,
        project: ResearchProject,
        batch: RevisionTargetBatch,
        issues: list[RevisionIssue],
        old_artifact: Any,
        new_artifact: Any,
        artifact_version: int,
        deterministic_results: list[RevisionCriterionResult],
    ) -> AgentRun[RevisionVerificationResult]:
        result = self.client.call(
            agent_name=self.agent_name,
            instructions=(
                "Act as an independent revision verifier. Check only whether the new artifact explicitly "
                "satisfies each completion criterion. A model call or a general promise is not evidence. "
                "Return criterion-level JSON and do not infer execution that has not occurred."
            ),
            payload={
                "project_id": project.project_id,
                "planning_only": project.planning_only,
                "batch": batch.model_dump(mode="json"),
                "issues": [issue.model_dump(mode="json") for issue in issues],
                "old_artifact": old_artifact,
                "new_artifact": new_artifact,
                "deterministic_results": [item.model_dump(mode="json") for item in deterministic_results],
                "verification_rule": (
                    "Every criterion must cite concrete text or fields in the new artifact. "
                    "Do not pass requirements that merely promise future specification."
                ),
            },
            output_model=RevisionVerificationResult,
        )
        verified = result.value.model_copy(
            update={
                "verification_id": new_id("revision_verification"),
                "action_id": batch.batch_id,
                "target_artifact": batch.target,
                "artifact_version": artifact_version,
                "verification_method": "deterministic+independent_qwen",
                "verified_at": utc_now(),
            }
        )
        return AgentRun(output=verified, metadata=result.metadata)
