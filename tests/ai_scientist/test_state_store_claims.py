from pathlib import Path

import pytest
from pydantic import ValidationError

from src.ai_scientist.claim_graph import ClaimGraph
from src.ai_scientist.exceptions import InvalidTransitionError
from src.ai_scientist.project_store import ProjectStore
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import (
    Claim,
    Conclusion,
    EvidenceItem,
    ResearchEvent,
    ResearchPhase,
    ResearchProject,
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


def test_execution_adapter_never_fabricates_results() -> None:
    adapter = ExecutionAdapter()
    assert adapter.capabilities()["execution_available"] is False
    with pytest.raises(NotImplementedError, match="No execution backend"):
        adapter.execute({"task": "run"})


def test_orchestrator_waits_for_execution_and_supports_planning_only(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    item = orchestrator.create_project("Plan a study", planning_only=True)
    item.phase = ResearchPhase.HUMAN_APPROVAL
    orchestrator.store.save(item)

    approved = orchestrator.approve_project(item.project_id)
    assert approved.phase == ResearchPhase.EXECUTION_WAITING
    step = orchestrator.run_next_step(item.project_id)
    assert step["current_phase"] == ResearchPhase.SYNTHESIS.value

    waiting = orchestrator.create_project("Wait for real execution", planning_only=False)
    waiting.phase = ResearchPhase.EXECUTION_WAITING
    orchestrator.store.save(waiting)
    step = orchestrator.run_next_step(waiting.project_id)
    assert step["current_phase"] == ResearchPhase.EXECUTION_WAITING.value
    assert step["stage_status"] == "waiting_for_execution_or_data"
