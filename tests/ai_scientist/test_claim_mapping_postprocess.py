from pathlib import Path

import pytest

from src.ai_scientist.exceptions import InvalidEvidenceReferenceError
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import (
    Claim,
    ClaimEvidenceLink,
    ClaimEvidenceMappingResult,
    ClaimItem,
    EvidenceCollection,
    EvidenceItem,
    ResearchPhase,
    ResearchQuestion,
)
from src.ai_scientist.structured_client import StructuredCallMetadata
from src.ai_scientist.schemas import utc_now


def metadata() -> StructuredCallMetadata:
    now = utc_now()
    return StructuredCallMetadata(
        agent_name="evidence_researcher",
        requested_model="test-model",
        actual_model="test-model",
        fallback_used=False,
        started_at=now,
        finished_at=now,
        model_calls=1,
        attempted_calls=1,
        successful_calls=1,
    )


def ready_project(orchestrator: ResearchOrchestrator) -> str:
    project = orchestrator.create_project("Map claim evidence", planning_only=True)
    project.phase = ResearchPhase.CLAIM_EVIDENCE_MAPPING
    project.question = ResearchQuestion(
        original_question="Does A improve B?",
        normalized_question="Does A improve B?",
    )
    project.evidence = [
        EvidenceItem(evidence_id="EVD-001", title="Source 1", summary="A improves B.", source_type="paper")
    ]
    orchestrator.store.save(project)
    return project.project_id


def mapping_result(evidence_id: str = "EVD-001") -> ClaimEvidenceMappingResult:
    return ClaimEvidenceMappingResult(
        claims=[
            ClaimItem(
                claim_id="tmp_claim",
                statement="A improves B.",
                claim_type="reported_fact",
                importance="high",
                status="supported",
            )
        ],
        links=[
            ClaimEvidenceLink(
                claim_id="tmp_claim",
                evidence_id=evidence_id,
                relation="supports",
                strength=0.8,
                rationale="The source reports the effect.",
            )
        ],
        evidence_coverage=1.0,
    )


def test_claim_mapping_success_saves_artifacts_and_advances(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project_id = ready_project(orchestrator)

    def fake_mapping(self, project, collection, track_failure_budget=True):
        return mapping_result(), metadata()

    monkeypatch.setattr(ResearchOrchestrator, "_run_claim_mapping", fake_mapping)

    result = orchestrator.run_next_step(project_id)
    updated = orchestrator.get_project(project_id)

    assert result["current_phase"] == ResearchPhase.HYPOTHESIS_GENERATION.value
    assert updated.phase == ResearchPhase.HYPOTHESIS_GENERATION
    assert updated.claims[0].claim_id == "CLM-001"
    assert updated.claims[0].supporting_evidence_ids == ["EVD-001"]
    assert updated.active_artifact_versions["claim_evidence_mapping"] == 1
    assert updated.active_artifact_versions["claim_graph"] == 1
    artifact_types = {item.artifact_type for item in updated.artifacts}
    assert {"claim_evidence_mapping", "claim_graph"} <= artifact_types


def test_duplicate_evidence_id_returns_specific_error(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    collection = EvidenceCollection(
        evidence_items=[
            EvidenceItem(evidence_id="EVD-001", title="Source 1", summary="one"),
            EvidenceItem(evidence_id="EVD-001", title="Source 2", summary="two"),
        ]
    )

    with pytest.raises(Exception) as exc_info:
        orchestrator._validate_evidence_ids(collection)

    assert getattr(exc_info.value, "cause_type", None) == "DuplicateEvidenceId"
    assert getattr(exc_info.value, "substep", None) == "evidence_reference_validation"


def test_invalid_claim_evidence_reference_is_typed(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    collection = EvidenceCollection(
        evidence_items=[EvidenceItem(evidence_id="EVD-001", title="Source 1", summary="one")]
    )
    mapping = mapping_result("EVD-404")

    with pytest.raises(InvalidEvidenceReferenceError) as exc_info:
        orchestrator._canonicalize_claim_mapping(mapping, collection)

    assert exc_info.value.claim_id == "CLM-001"
    assert exc_info.value.evidence_id == "EVD-404"


def test_new_evidence_marks_downstream_artifacts_stale(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("stale test", planning_only=True)

    orchestrator._mark_downstream_artifacts_stale(project, "evidence")

    assert project.active_artifact_versions["claim_evidence_mapping"] is None
    assert "claim_evidence_mapping" in project.stale_artifacts
    assert "hypotheses" in project.stale_artifacts


def test_debug_claim_mapping_does_not_change_project_phase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project_id = ready_project(orchestrator)

    def fake_mapping(self, project, collection, track_failure_budget=True):
        assert track_failure_budget is False
        return mapping_result(), metadata()

    monkeypatch.setattr(ResearchOrchestrator, "_run_claim_mapping", fake_mapping)

    result = orchestrator.debug_claim_mapping(project_id)
    updated = orchestrator.get_project(project_id)

    assert result["status"] == "ok"
    assert result["debug"]["schema_valid"] is True
    assert updated.phase == ResearchPhase.CLAIM_EVIDENCE_MAPPING
    assert updated.artifacts == []


def test_postprocess_failure_keeps_model_success_event_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project_id = ready_project(orchestrator)

    def fake_mapping(self, project, collection, track_failure_budget=True):
        return mapping_result("EVD-404"), metadata()

    monkeypatch.setattr(ResearchOrchestrator, "_run_claim_mapping", fake_mapping)

    with pytest.raises(InvalidEvidenceReferenceError):
        orchestrator.run_next_step(project_id)

    updated = orchestrator.get_project(project_id)
    events = orchestrator.list_events(project_id)

    assert updated.phase == ResearchPhase.CLAIM_EVIDENCE_MAPPING
    assert any(event.status == "claim_mapping_model_completed" and event.successful_calls == 1 for event in events)
    failed = [event for event in events if event.status == "failed"][-1]
    assert failed.failure_category == "orchestration_postprocess_error"
    assert failed.stage_substep == "evidence_reference_validation"
    assert failed.provider_error_code == "InvalidEvidenceReferenceError"
