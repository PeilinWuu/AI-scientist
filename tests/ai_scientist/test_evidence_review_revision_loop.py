from pathlib import Path

from src.ai_scientist.evidence_verifier import deduplicate_evidence, verify_evidence_item
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.quality import apply_reviewer_quality_gates, compute_quality_metrics, enrich_evidence_items
from src.ai_scientist.schemas import (
    Claim,
    EvidenceItem,
    ResearchPhase,
    ResearchProject,
    ReviewResult,
    RevisionAction,
    SystematicReviewProtocol,
)
from src.ai_scientist.stagnation_detector import build_revision_snapshot, detect_stagnation


def test_unverified_evidence_without_identifier_cannot_be_a_or_b() -> None:
    item = EvidenceItem(title="Untraceable source", summary="summary", is_primary_source=True)
    graded = enrich_evidence_items([item])[0]

    assert graded.verification_status == "unverified"
    assert graded.source_level not in {"A", "B"}
    assert graded.is_primary_source is False


def test_verified_false_source_not_counted_in_primary_ratio() -> None:
    project = ResearchProject(title="T", objective="O")
    project.evidence = enrich_evidence_items(
        [EvidenceItem(title="No ID", summary="summary", source_type="paper", is_primary_source=True)]
    )
    project.claims = [
        Claim(
            statement="Supported",
            claim_type="reported_fact",
            status="supported",
            supporting_evidence_ids=[project.evidence[0].evidence_id],
        )
    ]

    metrics = compute_quality_metrics(project)

    assert metrics.primary_source_ratio == 0
    assert metrics.verified_primary_source_count == 0


def test_doi_verification_allows_a_level_primary_source() -> None:
    item = EvidenceItem(
        title="Traceable paper",
        summary="summary",
        source_type="paper",
        doi="https://doi.org/10.1000/example",
        is_primary_source=True,
    )
    verified = verify_evidence_item(item)
    graded = enrich_evidence_items([verified])[0]

    assert graded.doi == "10.1000/example"
    assert graded.verification_status == "verified"
    assert graded.source_level == "A"


def test_duplicate_doi_does_not_count_twice() -> None:
    evidence = deduplicate_evidence(
        [
            verify_evidence_item(EvidenceItem(evidence_id="E1", title="A", summary="summary", doi="10.1000/x")),
            verify_evidence_item(EvidenceItem(evidence_id="E2", title="A duplicate", summary="summary", doi="https://doi.org/10.1000/x")),
        ]
    )

    assert evidence[0].duplicate_of is None
    assert evidence[1].duplicate_of == "E1"


def test_reviewer_quality_gates_preserve_multiple_revision_actions() -> None:
    review = ReviewResult(
        evidence_quality_score=5,
        methodological_validity_score=5,
        feasibility_score=8,
        reproducibility_score=5,
        claim_support_score=7,
        uncertainty_handling_score=7,
        decision="revise_evidence",
        blocking_issues=[
            "Evidence sources lack DOI or PMID.",
            "Systematic review protocol needs Boolean search strings and screening process.",
            "Analysis plan conflates structural existence and clinical efficacy.",
        ],
        required_revision_target="evidence",
        revision_plan=[
            RevisionAction(target="evidence", priority=1, reason="Verify sources."),
            RevisionAction(target="reproducibility_plan", priority=2, reason="Add protocol."),
            RevisionAction(target="analysis_plan", priority=3, reason="Separate dimensions."),
        ],
    )
    metrics = compute_quality_metrics(ResearchProject(title="T", objective="O"))

    gated = apply_reviewer_quality_gates(review, metrics)

    assert [item.target for item in gated.revision_plan][:3] == [
        "evidence",
        "reproducibility_plan",
        "analysis_plan",
    ]


def test_orchestrator_stops_at_human_review_with_multiple_targets(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Queue")
    project.phase = ResearchPhase.FEASIBILITY_REVIEW
    project.quality_metrics = compute_quality_metrics(project)
    review = ReviewResult(
        evidence_quality_score=5,
        methodological_validity_score=6,
        feasibility_score=6,
        reproducibility_score=5,
        claim_support_score=6,
        uncertainty_handling_score=6,
        decision="revise_evidence",
        blocking_issues=["Need evidence and protocol."],
        required_revision_target="evidence",
        revision_plan=[
            RevisionAction(target="evidence", priority=1, reason="Verify sources."),
            RevisionAction(target="analysis_plan", priority=2, reason="Separate claims."),
        ],
    )

    result = orchestrator._apply_review_decision(project, review)

    assert result["revision_required"] is True
    assert project.phase == ResearchPhase.HUMAN_REVISION_REVIEW
    assert project.current_revision_action is None
    assert {item.target for item in project.revision_issues} == {"evidence", "analysis_plan"}


def test_human_approval_batches_revision_issues_by_target(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Queue advance")
    project.phase = ResearchPhase.FEASIBILITY_REVIEW
    project.quality_metrics = compute_quality_metrics(project)
    review = ReviewResult(
        evidence_quality_score=5,
        methodological_validity_score=5,
        feasibility_score=6,
        reproducibility_score=6,
        claim_support_score=6,
        uncertainty_handling_score=6,
        decision="revise_evidence",
        blocking_issues=["Need verified evidence and a separate analysis plan."],
        required_revision_target="evidence",
        revision_plan=[
            RevisionAction(target="evidence", priority=1, reason="Verify sources."),
            RevisionAction(target="analysis_plan", priority=2, reason="Separate claim dimensions."),
        ],
    )

    orchestrator._apply_review_decision(project, review)
    orchestrator.store.save(project)
    from src.ai_scientist.schemas import RevisionIssueDecision

    decisions = [
        RevisionIssueDecision(issue_id=item.issue_id, disposition="accept_ai")
        for item in project.revision_issues
    ]
    updated = orchestrator.submit_revision_review(project.project_id, decisions)

    assert {item.target for item in updated.approved_revision_plans[-1].target_batches} == {
        "evidence", "analysis_plan"
    }
    assert updated.iteration == 1
    assert updated.phase == ResearchPhase.REVISION


def test_conclusion_traceability_gate_waits_for_synthesis() -> None:
    metrics = compute_quality_metrics(ResearchProject(title="T", objective="O"))

    failed = apply_reviewer_quality_gates(
        ReviewResult(
            evidence_quality_score=8,
            methodological_validity_score=8,
            feasibility_score=8,
            reproducibility_score=8,
            claim_support_score=8,
            uncertainty_handling_score=8,
            decision="approve",
        ),
        metrics,
    ).failed_quality_gates

    assert "conclusion_traceability_below_0.9" not in failed


def test_systematic_review_protocol_artifact_model_is_available() -> None:
    protocol = SystematicReviewProtocol(
        review_question="What evidence exists?",
        databases=["PubMed", "Web of Science"],
        boolean_search_strings=["soft robot AND swimmer"],
        inclusion_criteria=["peer reviewed"],
        screening_process=["title screening", "full text screening"],
    )

    assert protocol.review_question == "What evidence exists?"
    assert "PubMed" in protocol.databases
    assert protocol.boolean_search_strings


def test_stagnation_detector_pauses_after_repeated_no_progress() -> None:
    project = ResearchProject(title="T", objective="O")
    review = ReviewResult(
        evidence_quality_score=5,
        methodological_validity_score=6,
        feasibility_score=6,
        reproducibility_score=6,
        claim_support_score=5,
        uncertainty_handling_score=6,
        decision="revise_evidence",
        blocking_issues=["Sources are unverifiable."],
        required_revision_target="evidence",
        revision_plan=[RevisionAction(target="evidence", priority=1, reason="Need verification.")],
    )
    project.reviews.append(review)
    project.pending_revision_actions = review.revision_plan
    project.quality_metrics = compute_quality_metrics(project)
    project.revision_snapshots.append(build_revision_snapshot(project))
    project.iteration = 1
    project.revision_snapshots.append(build_revision_snapshot(project))

    assert detect_stagnation(project) is True


def test_user_visible_events_are_deduplicated(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Events")
    from src.ai_scientist.events import completed_event

    event = completed_event(
        project.project_id,
        ResearchPhase.BACKGROUND_RESEARCH,
        "evidence_researcher",
        visibility="user",
        display_key="background_research_completed",
        summary_markdown="done",
    )
    orchestrator._append_event(project, event)
    orchestrator._append_event(project, event.model_copy(update={"event_id": "event_second"}))

    assert len(orchestrator.list_events(project.project_id)) == 2  # created + first user event
