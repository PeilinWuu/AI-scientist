from pathlib import Path

from src.ai_scientist.agents.base_agent import AgentRun
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.quality import enrich_evidence_items
from src.ai_scientist.schemas import (
    Claim,
    EvidenceItem,
    Hypothesis,
    ResearchMode,
    ResearchPhase,
    ReproducibilityOutput,
    ReviewResult,
)
from src.ai_scientist.structured_client import StructuredCallMetadata
from src.ai_scientist.schemas import utc_now


def metadata(agent_name: str) -> StructuredCallMetadata:
    now = utc_now()
    return StructuredCallMetadata(
        agent_name=agent_name,
        requested_model=f"{agent_name}-model",
        actual_model=f"{agent_name}-model",
        fallback_used=False,
        started_at=now,
        finished_at=now,
        model_calls=1,
        attempted_calls=1,
        successful_calls=1,
    )


def ready_project(orchestrator: ResearchOrchestrator, decision: str, max_iterations: int = 2):
    project = orchestrator.create_project("Review revision flow", max_iterations=max_iterations, planning_only=True)
    project.phase = ResearchPhase.FEASIBILITY_REVIEW
    project.research_mode = ResearchMode.THEORETICAL
    evidence = EvidenceItem(
        evidence_id="e1",
        title="Journal article with DOI",
        source_type="paper",
        source_url="https://doi.org/10.1234/example",
        citation="doi:10.1234/example",
        summary="summary",
    )
    project.evidence = enrich_evidence_items([evidence])
    project.claims = [
        Claim(
            claim_id="c1",
            statement="Supported claim",
            claim_type="reported_fact",
            supporting_evidence_ids=["e1"],
            status="supported",
        )
    ]
    project.hypotheses = [
        Hypothesis(
            hypothesis_id="h1",
            statement="Falsifiable hypothesis",
            mechanism="mechanism",
            predictions=["prediction"],
            falsification_conditions=["condition"],
            alternative_explanations=["alternative"],
            required_evidence=["evidence"],
        )
    ]
    orchestrator.store.save(project)

    def fake_run_agent(project_arg, agent_class):
        if agent_class.__name__ == "ReproducibilityEngineerAgent":
            return AgentRun(
                output=ReproducibilityOutput(
                    reproducibility_plan=["Archive all prompts and evidence artifacts."],
                    execution_readiness="planning_only",
                ),
                metadata=metadata("reproducibility_engineer"),
            )
        return AgentRun(
            output=ReviewResult(
                evidence_quality_score=8,
                methodological_validity_score=8,
                feasibility_score=8,
                reproducibility_score=8,
                claim_support_score=8,
                uncertainty_handling_score=8,
                decision=decision,
                blocking_issues=[] if decision == "approve" else ["Needs revision."],
                required_revision_target=decision.replace("revise_", "") if decision.startswith("revise_") else "none",
            ),
            metadata=metadata("skeptical_reviewer"),
        )

    return project, fake_run_agent


def run_review(tmp_path: Path, decision: str, max_iterations: int = 2, iteration: int = 0):
    orchestrator = ResearchOrchestrator(tmp_path)
    project, fake_run_agent = ready_project(orchestrator, decision, max_iterations=max_iterations)
    project.iteration = iteration
    orchestrator.store.save(project)
    orchestrator._run_agent = fake_run_agent  # type: ignore[method-assign]
    result = orchestrator.run_next_step(project.project_id)
    return result, orchestrator.get_project(project.project_id)


def test_reviewer_revise_evidence_enters_human_revision_review(tmp_path: Path) -> None:
    result, project = run_review(tmp_path, "revise_evidence")

    assert result["current_phase"] == ResearchPhase.HUMAN_REVISION_REVIEW.value
    assert result["revision_required"] is True
    assert result["review_decision"] == "revise_evidence"
    assert result["iteration"] == 0
    assert project.phase != ResearchPhase.FAILED


def test_reviewer_revise_hypothesis_waits_for_human_review(tmp_path: Path) -> None:
    result, project = run_review(tmp_path, "revise_hypothesis")

    assert result["current_phase"] == ResearchPhase.HUMAN_REVISION_REVIEW.value
    assert project.phase == ResearchPhase.HUMAN_REVISION_REVIEW


def test_reviewer_revise_method_waits_for_human_review(tmp_path: Path) -> None:
    result, project = run_review(tmp_path, "revise_method")

    assert result["current_phase"] == ResearchPhase.HUMAN_REVISION_REVIEW.value
    assert project.phase == ResearchPhase.HUMAN_REVISION_REVIEW


def test_reviewer_revise_design_waits_for_human_review(tmp_path: Path) -> None:
    result, project = run_review(tmp_path, "revise_design")

    assert result["current_phase"] == ResearchPhase.HUMAN_REVISION_REVIEW.value
    assert project.phase == ResearchPhase.HUMAN_REVISION_REVIEW


def test_reviewer_reject_enters_failed(tmp_path: Path) -> None:
    result, project = run_review(tmp_path, "reject")

    assert result["current_phase"] == ResearchPhase.FAILED.value
    assert result["revision_required"] is False
    assert project.phase == ResearchPhase.FAILED


def test_reviewer_revision_exhaustion_is_explicit(tmp_path: Path) -> None:
    result, project = run_review(tmp_path, "revise_evidence", max_iterations=1, iteration=1)

    assert result["current_phase"] == ResearchPhase.HUMAN_INTERVENTION_REQUIRED.value
    assert result["max_revision_exhausted"] is True
    assert project.phase == ResearchPhase.HUMAN_INTERVENTION_REQUIRED


def test_provided_data_resumes_human_intervention_at_independent_re_review(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Resume after data", planning_only=True)
    project.phase = ResearchPhase.HUMAN_INTERVENTION_REQUIRED
    project.iteration = 2
    orchestrator.store.save(project)

    updated = orchestrator.provide_data(
        project.project_id,
        ["assets/observations.csv"],
        "Seeded damped-oscillator observations",
        "text/csv",
    )

    assert updated.phase == ResearchPhase.CRITICAL_REVIEW
    assert updated.iteration == 2
    assert updated.artifacts[-1].artifact_type == "provided_data_manifest"
    assert any("重新进行独立复审" in message for message in updated.stage_messages)


def test_planning_only_missing_execution_tools_does_not_fail_review(tmp_path: Path) -> None:
    result, project = run_review(tmp_path, "approve")

    assert result["current_phase"] == ResearchPhase.HUMAN_APPROVAL.value
    assert project.phase == ResearchPhase.HUMAN_APPROVAL
    assert project.planning_only is True
