from pathlib import Path

import pytest
from pydantic import ValidationError

from src.ai_scientist.claim_graph import ClaimGraph
from src.ai_scientist.exceptions import InvalidReviewDecisionError, InvalidTransitionError
from src.ai_scientist.quality import apply_reviewer_quality_gates, compute_quality_metrics, enrich_evidence_items
from src.ai_scientist.quality import failed_quality_gates
from src.ai_scientist.project_store import ProjectStore
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.report_writer import build_research_plan_markdown
from src.ai_scientist.schemas import (
    Claim,
    Conclusion,
    EvidenceItem,
    ResearchEvent,
    ResearchPhase,
    ResearchProject,
    ResearchQuestion,
    ReviewResult,
)
from src.ai_scientist.state_machine import ResearchStateMachine
from src.ai_scientist.tools.execution_adapter import ExecutionAdapter


def project(phase: ResearchPhase = ResearchPhase.INTAKE, max_iterations: int = 2) -> ResearchProject:
    return ResearchProject(
        title="Test",
        objective="Test objective",
        phase=phase,
        max_iterations=max_iterations,
        budget={"max_model_calls": 10, "max_iterations": max_iterations},
    )


def test_state_machine_blocks_skips_terminals_and_excess_revision() -> None:
    machine = ResearchStateMachine()
    item = project()
    assert machine.transition(item, "next") == ResearchPhase.QUESTION_FORMULATION
    with pytest.raises(InvalidTransitionError):
        machine.transition(item, "approve")

    cancelled = project(ResearchPhase.CANCELLED)
    with pytest.raises(InvalidTransitionError):
        machine.transition(cancelled, "next")

    exhausted = project(ResearchPhase.FEASIBILITY_REVIEW, max_iterations=0)
    assert machine.transition(exhausted, "revise_design") == ResearchPhase.FAILED

    review = project(ResearchPhase.FEASIBILITY_REVIEW)
    with pytest.raises(InvalidReviewDecisionError):
        machine.transition(review, "unknown_decision")

    critical = project(ResearchPhase.CRITICAL_REVIEW)
    assert machine.transition(critical, "revise_evidence") == ResearchPhase.BACKGROUND_RESEARCH


def test_project_store_save_restore_atomic_and_append(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    item = project()
    path = store.save(item)
    assert path.exists()
    assert store.load(item.project_id) == item
    assert not list(path.parent.glob("*.tmp"))

    event = ResearchEvent(
        project_id=item.project_id,
        phase=item.phase,
        agent_name="test",
        status="completed",
    )
    store.append_event(event)
    store.append_event(event.model_copy(update={"event_id": "event_second"}))
    assert len(store.list_events(item.project_id)) == 2


def test_claim_graph_requires_evidence_and_protects_conclusion() -> None:
    evidence = EvidenceItem(
        evidence_id="evidence_1",
        title="Source",
        source_type="paper",
        summary="summary",
    )
    supported = Claim(
        claim_id="claim_1",
        statement="Supported statement",
        claim_type="reported_fact",
        supporting_evidence_ids=[evidence.evidence_id],
        status="supported",
    )
    invalid = Claim(
        claim_id="claim_2",
        statement="Unlinked statement",
        claim_type="conclusion",
        status="supported",
    )
    graph = ClaimGraph([evidence], [supported, invalid])
    assert graph.validate() == ["Supported claim claim_2 has no evidence."]
    conclusion = Conclusion(supported_findings=[supported.statement, invalid.statement])
    assert graph.validate_conclusion_traceability(conclusion) == [invalid.statement]


def test_reviewer_cannot_approve_low_score() -> None:
    with pytest.raises(ValidationError):
        ReviewResult(
            evidence_quality_score=5,
            methodological_validity_score=8,
            feasibility_score=8,
            reproducibility_score=8,
            claim_support_score=8,
            uncertainty_handling_score=8,
            decision="approve",
        )


def test_execution_adapter_exposes_only_controlled_operations() -> None:
    adapter = ExecutionAdapter()
    capabilities = adapter.capabilities()
    assert capabilities["execution_available"] is True
    assert capabilities["arbitrary_code_execution"] is False
    result = adapter.execute({"task": "run"})
    assert result["status"] == "rejected"
    assert result["artifacts"] == []


def test_orchestrator_waits_for_execution_and_supports_planning_only(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    item = orchestrator.create_project("Plan a study", planning_only=True)
    item.phase = ResearchPhase.HUMAN_APPROVAL
    item.reviews.append(
        ReviewResult(
            evidence_quality_score=8,
            methodological_validity_score=8,
            feasibility_score=8,
            reproducibility_score=8,
            claim_support_score=8,
            uncertainty_handling_score=8,
            decision="approve",
        )
    )
    item.active_artifact_versions.update(
        {
            "research_question": 1,
            "evidence_map": 1,
            "claim_evidence_mapping": 1,
            "hypotheses": 1,
            "study_design": 1,
            "analysis_plan": 1,
            "reproducibility_plan": 1,
            "independent_review": 1,
        }
    )
    orchestrator.store.save(item)

    package = orchestrator.get_review_package(item.project_id)
    approved = orchestrator.approve_project(
        item.project_id,
        acknowledged=True,
        expected_versions=package.artifact_versions,
    )
    assert approved.phase == ResearchPhase.SYNTHESIS

    waiting = orchestrator.create_project("Wait for real execution", planning_only=False)
    waiting.phase = ResearchPhase.EXECUTION_WAITING
    orchestrator.store.save(waiting)
    step = orchestrator.run_next_step(waiting.project_id)
    assert step["current_phase"] == ResearchPhase.EXECUTION_WAITING.value
    assert step["stage_status"] == "waiting_for_execution_or_data"


def test_quality_metrics_source_grading_and_reviewer_gates() -> None:
    evidence = EvidenceItem(
        evidence_id="evidence_primary",
        title="Journal article with DOI",
        source_type="paper",
        source_url="https://doi.org/10.1234/example",
        citation="doi:10.1234/example",
        summary="summary",
    )
    item = project()
    item.evidence = enrich_evidence_items([evidence])
    item.claims = [
        Claim(
            claim_id="claim_supported",
            statement="Supported claim",
            claim_type="reported_fact",
            supporting_evidence_ids=["evidence_primary"],
            status="supported",
        )
    ]
    item.hypotheses = []
    metrics = compute_quality_metrics(item)
    assert metrics.evidence_coverage == 1
    assert metrics.primary_source_ratio == 1
    assert item.evidence[0].source_level == "A"

    review = ReviewResult(
        evidence_quality_score=8,
        methodological_validity_score=8,
        feasibility_score=8,
        reproducibility_score=8,
        claim_support_score=8,
        uncertainty_handling_score=8,
        decision="approve",
    )
    gated = apply_reviewer_quality_gates(review, metrics)
    assert gated.decision == "revise_hypothesis"
    assert "hypothesis_completeness_below_0.8" in gated.failed_quality_gates


def test_missing_conclusion_does_not_fail_traceability_gate() -> None:
    item = project()
    metrics = compute_quality_metrics(item)

    assert metrics.total_conclusions == 0
    assert metrics.conclusion_traceability == 0
    assert "conclusion_traceability_below_0.9" not in failed_quality_gates(metrics, None)


def test_human_edit_versions_artifacts_and_targeted_rollback(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    item = orchestrator.create_project("Edit question", planning_only=True)
    item.phase = ResearchPhase.HUMAN_APPROVAL
    item.question = item.question or ResearchQuestion(
        original_question="old",
        normalized_question="old",
    )
    orchestrator.store.save(item)

    edited = orchestrator.patch_question(item.project_id, {"normalized_question": "new"}, "tighten scope")

    assert edited.phase == ResearchPhase.QUESTION_FORMULATION
    assert edited.question.normalized_question == "new"
    artifact_files = sorted((tmp_path / item.project_id / "artifacts").glob("question_v*.json"))
    assert artifact_files
    events = orchestrator.list_events(item.project_id)
    assert any(event.status == "human_edit" for event in events)
    assert any(event.status == "targeted_rollback" for event in events)


def test_research_plan_report_declares_no_real_execution() -> None:
    item = project(ResearchPhase.COMPLETED)
    report = build_research_plan_markdown(item)
    assert "No real experiment, simulation, or data analysis has been executed" in report
    assert "must not be treated as an experimental conclusion" in report


def test_research_plan_report_renders_controlled_execution_results() -> None:
    item = project(ResearchPhase.COMPLETED)
    item.executor_binding = "damped_oscillator_v1"
    item.internal_execution_summary = {
        "executor_binding": "damped_oscillator_v1",
        "run_id": "run-1",
        "status": "complete",
        "seed": 17,
        "observation_asset_id": "asset-1",
        "round_1": {
            "execution_id": "exec-1",
            "actual_parameters": {
                "damping_min": 0.05,
                "damping_max": 0.35,
                "omega_min": 2.0,
                "omega_max": 2.8,
            },
            "metrics": {"best_damping": 0.2, "best_omega": 2.4, "rmse": 0.057, "evaluations": 63},
        },
        "round_2": {
            "execution_id": "exec-2",
            "actual_parameters": {
                "damping_min": 0.15,
                "damping_max": 0.25,
                "omega_min": 2.3,
                "omega_max": 2.5,
            },
            "metrics": {"best_damping": 0.17, "best_omega": 2.36, "rmse": 0.033, "evaluations": 121},
        },
        "comparison": {
            "iteration": {
                "absolute_rmse_gain": 0.024,
                "relative_rmse_gain_percent": 42.1,
                "round_1_evaluations": 63,
                "round_2_evaluations": 121,
            }
        },
    }

    report = build_research_plan_markdown(item)

    assert "No real experiment, simulation, or data analysis has been executed" not in report
    assert "## Controlled Execution Results" in report
    assert "`run-1`" in report
    assert "0.057" in report
    assert "0.033" in report
    assert "Total two-round evaluations: `184`" in report
