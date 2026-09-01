from __future__ import annotations

from pathlib import Path

import pytest

from src.ai_scientist.agents.base_agent import AgentRun
from src.ai_scientist.job_store import ResearchJobStore
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.revision_workflow import (
    build_approved_revision_plan,
    combine_revision_verification_results,
    deterministic_verify_batch,
    normalize_revision_plan,
)
from src.ai_scientist.schemas import (
    AnalysisPlan,
    ApprovedRevisionPlan,
    ResearchPhase,
    ReviewResult,
    RevisionAction,
    RevisionCriterionResult,
    RevisionIssue,
    RevisionIssueDecision,
    RevisionTargetBatch,
    RevisionVerificationResult,
    utc_now,
)
from src.ai_scientist.structured_client import StructuredCallMetadata


def metadata(agent_name: str = "analyst") -> StructuredCallMetadata:
    now = utc_now()
    return StructuredCallMetadata(
        agent_name=agent_name,
        requested_model="qwen-test",
        actual_model="qwen-test",
        fallback_used=False,
        started_at=now,
        finished_at=now,
        model_calls=1,
        attempted_calls=1,
        successful_calls=1,
    )


def review_with(*actions: RevisionAction) -> ReviewResult:
    return ReviewResult(
        evidence_quality_score=8,
        methodological_validity_score=7,
        feasibility_score=7,
        reproducibility_score=5,
        claim_support_score=8,
        uncertainty_handling_score=8,
        blocking_issues=[action.reason for action in actions],
        non_blocking_issues=[],
        recommendations=[],
        decision="revise_method",
        required_revision_target="method",
        revision_plan=list(actions),
    )


@pytest.mark.parametrize(
    ("reason", "changes", "expected_target", "expected_classification"),
    [
        ("Missing Boolean database searches", ["Provide PubMed and Cochrane Boolean strings"], "reproducibility_plan", "plan_blocking"),
        ("Missing SD imputation", ["Specify correlation coefficient and fallback"], "analysis_plan", "plan_blocking"),
        ("Missing intensity harmonization", ["Map %HRmax, %HRR, METs and RPE"], "analysis_plan", "plan_blocking"),
        ("Missing random-effects estimator", ["Pre-specify REML and continuity correction"], "analysis_plan", "plan_blocking"),
        ("No overlap rule", ["Create a citation matrix for overlapping studies"], "reproducibility_plan", "plan_blocking"),
        ("Unsupported hypotheses", ["Mark unsupported hypotheses exploratory"], "hypothesis", "plan_blocking"),
        ("Register PROSPERO", ["Register PROSPERO"], "execution_requirements", "execution_prerequisite"),
        ("Actually execute meta-analysis", ["Execute quantitative meta-analysis"], "execution_requirements", "execution_prerequisite"),
    ],
)
def test_real_reviewer_issue_routing(
    reason: str, changes: list[str], expected_target: str, expected_classification: str
) -> None:
    issues = normalize_revision_plan(
        review_with(RevisionAction(target="method", priority=1, reason=reason, required_changes=changes)),
        planning_only=True,
    )
    assert issues[0].target == expected_target
    assert issues[0].classification == expected_classification


def test_system_quality_gate_is_not_revision_issue() -> None:
    review = review_with(
        RevisionAction(
            target="method",
            reason="Quality gate failed: blocking_issues_present",
            required_changes=["Quality gate failed: reviewer_score_below_6"],
        ),
        RevisionAction(target="analysis_plan", reason="Specify REML estimator."),
    )
    issues = normalize_revision_plan(review, planning_only=True)
    assert len(issues) == 1
    assert "Quality gate" not in issues[0].problem


def test_duplicate_revision_issues_are_merged() -> None:
    review = review_with(
        RevisionAction(target="method", reason="Language policy and translation resources are missing.", priority=3),
        RevisionAction(target="method", reason="Finalize language and translation policy.", priority=3),
    )
    assert len(normalize_revision_plan(review, planning_only=True)) == 1


def test_reviewer_revision_stops_at_human_gate_without_starting_action(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Human gate")
    project.phase = ResearchPhase.FEASIBILITY_REVIEW
    result = orchestrator._apply_review_decision(
        project,
        review_with(RevisionAction(target="analysis_plan", reason="Specify REML estimator.")),
    )
    assert result["revision_required"] is True
    assert project.phase == ResearchPhase.HUMAN_REVISION_REVIEW
    assert project.current_revision_action is None
    assert project.iteration == 0


def test_run_next_at_human_revision_gate_does_not_call_model(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("No model at gate")
    project.phase = ResearchPhase.HUMAN_REVISION_REVIEW
    orchestrator.store.save(project)
    orchestrator._run_agent = lambda *args: (_ for _ in ()).throw(AssertionError("model called"))  # type: ignore[method-assign]
    result = orchestrator.run_next_step(project.project_id)
    assert result["stage_status"] == "awaiting_human_revision_review"
    assert result["current_phase"] == ResearchPhase.HUMAN_REVISION_REVIEW.value


@pytest.mark.parametrize(
    "disposition",
    ["accept_ai", "accept_modified", "provide_content", "accept_limitation", "defer_execution", "reject"],
)
def test_all_human_revision_dispositions_are_persisted(disposition: str, tmp_path: Path) -> None:
    project = ResearchOrchestrator(tmp_path).create_project("Dispositions")
    issue = RevisionIssue(
        classification="plan_blocking",
        target="analysis_plan",
        problem="Specify estimator.",
        reviewer_recommendations=["Use REML."],
    )
    project.revision_issues = [issue]
    text = "Use REML." if disposition in {"accept_modified", "provide_content"} else "Documented reason."
    decision = RevisionIssueDecision(
        issue_id=issue.issue_id,
        disposition=disposition,
        instruction=text if disposition in {"accept_modified", "provide_content"} else "",
        reason=text if disposition in {"accept_limitation", "defer_execution", "reject"} else "",
    )
    plan = build_approved_revision_plan(project, [decision])
    assert issue.status != "pending"
    assert plan.created_by == "human"


def test_every_issue_requires_human_decision(tmp_path: Path) -> None:
    project = ResearchOrchestrator(tmp_path).create_project("Missing decision")
    project.revision_issues = [
        RevisionIssue(classification="plan_blocking", target="analysis_plan", problem="A"),
        RevisionIssue(classification="plan_blocking", target="methodology", problem="B"),
    ]
    with pytest.raises(ValueError, match="Every revision issue"):
        build_approved_revision_plan(
            project,
            [RevisionIssueDecision(issue_id=project.revision_issues[0].issue_id, disposition="accept_ai")],
        )


@pytest.mark.parametrize("disposition", ["accept_modified", "provide_content", "reject"])
def test_decisions_requiring_explanation_reject_empty_text(disposition: str, tmp_path: Path) -> None:
    project = ResearchOrchestrator(tmp_path).create_project("Validation")
    issue = RevisionIssue(classification="plan_blocking", target="analysis_plan", problem="A")
    project.revision_issues = [issue]
    with pytest.raises(ValueError, match="requires instructions or a reason"):
        build_approved_revision_plan(
            project, [RevisionIssueDecision(issue_id=issue.issue_id, disposition=disposition)]
        )


def test_submit_persists_approved_plan_and_increments_one_cycle(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Persist plan")
    project.phase = ResearchPhase.HUMAN_REVISION_REVIEW
    project.revision_issues = [
        RevisionIssue(classification="plan_blocking", target="analysis_plan", problem="Specify REML."),
        RevisionIssue(classification="plan_blocking", target="analysis_plan", problem="Specify SD imputation."),
    ]
    orchestrator.store.save(project)
    decisions = [RevisionIssueDecision(issue_id=item.issue_id, disposition="accept_ai") for item in project.revision_issues]
    updated = orchestrator.submit_revision_review(project.project_id, decisions)
    persisted = orchestrator.get_project(project.project_id)
    assert updated.iteration == 1
    assert persisted.iteration == 1
    assert len(persisted.human_revision_history) == 1
    assert len(persisted.approved_revision_plans[-1].target_batches) == 1
    batch = persisted.approved_revision_plans[-1].target_batches[0]
    assert len(batch.issue_snapshots) == 2
    assert [item.issue_id for item in batch.issue_snapshots] == batch.issue_ids


def test_orphan_in_progress_without_active_job_recovers(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Orphan")
    project.phase = ResearchPhase.METHOD_SELECTION
    project.reviews = [review_with(RevisionAction(target="method", reason="Specify method."))]
    project.current_revision_action = RevisionAction(
        target="method", reason="Old action", status="in_progress"
    )
    orchestrator.store.save(project)
    recovered = orchestrator.get_project(project.project_id)
    assert recovered.phase == ResearchPhase.HUMAN_REVISION_REVIEW
    assert recovered.current_revision_action.status == "pending"
    assert recovered.active_job_id is None
    assert recovered.revision_recovery_messages


def test_legacy_orphan_iteration_is_reclaimed_before_human_approval(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Legacy cycle")
    project.iteration = 1
    project.budget.used_iterations = 1
    project.phase = ResearchPhase.METHOD_SELECTION
    project.reviews = [review_with(RevisionAction(target="method", reason="Specify method."))]
    project.current_revision_action = RevisionAction(target="method", reason="Old", status="in_progress")
    orchestrator.store.save(project)
    orchestrator.get_project(project.project_id)
    orchestrator.get_project(project.project_id)
    migrated = orchestrator.get_project(project.project_id)
    assert migrated.revision_migration_version == 5
    assert migrated.iteration == 0
    assert migrated.budget.used_iterations == 0


def test_in_progress_with_real_queued_job_is_not_recovered(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    jobs = ResearchJobStore(orchestrator.store.root)
    project = orchestrator.create_project("Active")
    project.phase = ResearchPhase.REVISION
    job = jobs.create(project.project_id, project.phase.value)
    project.active_job_id = job.job_id
    project.current_revision_action = RevisionAction(target="analysis_plan", reason="Active", status="in_progress")
    orchestrator.store.save(project)
    loaded = orchestrator.get_project(project.project_id)
    assert loaded.phase == ResearchPhase.REVISION
    assert loaded.current_revision_action.status == "in_progress"


@pytest.mark.parametrize(
    ("criterion", "artifact", "passed"),
    [
        ("Search strings are executable without ambiguity", {"query": "hypertension AND exercise", "databases": ["PubMed", "Cochrane"]}, True),
        ("Search strings are executable without ambiguity", {"strategy": "Use reproducible search"}, False),
        ("Imputation rules are documented with sources", {"rule": "impute missing SD using correlation coefficient 0.5; fallback sensitivity"}, True),
        ("Mapping table is provided with sources", {"mapping": "%HRmax %HRR METs RPE light moderate vigorous"}, True),
        ("Estimator and corrections are fixed in protocol", {"method": "REML with continuity correction"}, True),
        ("Environment details are documented", {"environment": "R 4.4; seed 42; package version locked"}, True),
    ],
)
def test_deterministic_criterion_verification(criterion: str, artifact: dict, passed: bool) -> None:
    issue = RevisionIssue(
        classification="plan_blocking",
        target="analysis_plan",
        problem="Verify",
        completion_criteria=[criterion],
    )
    result = deterministic_verify_batch(RevisionTargetBatch(target="analysis_plan"), [issue], artifact)
    assert result[0].passed is passed


def test_planning_only_completion_criteria_do_not_require_completed_execution() -> None:
    review = ReviewResult(
        evidence_quality_score=8,
        methodological_validity_score=8,
        feasibility_score=8,
        reproducibility_score=5,
        claim_support_score=8,
        uncertainty_handling_score=8,
        decision="revise_method",
        required_revision_target="method",
        revision_plan=[
            RevisionAction(
                target="analysis",
                reason="Pre-specify analysis execution.",
                completion_criteria=[
                    "Code implements pre-specified choices",
                    "Mapping is applied consistently in pilot extraction",
                    "Rules are pre-registered",
                ],
            )
        ],
    )
    issue = normalize_revision_plan(review, planning_only=True)[0]
    assert all("code implements" not in item.lower() for item in issue.completion_criteria)
    assert any("later execution code" in item.lower() for item in issue.completion_criteria)
    assert any("pilot extraction procedure" in item.lower() for item in issue.completion_criteria)
    assert any("registration destination" in item.lower() for item in issue.completion_criteria)


def test_existing_planning_project_migrates_execution_only_criteria(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Legacy planning revision", planning_only=True)
    project.phase = ResearchPhase.HUMAN_REVISION_REVIEW
    project.revision_migration_version = 3
    project.revision_issues = [
        RevisionIssue(
            classification="plan_blocking",
            target="analysis_plan",
            problem="Legacy criterion",
            completion_criteria=["Code implements pre-specified choices"],
        )
    ]
    orchestrator.store.save(project)

    migrated = orchestrator.get_project(project.project_id)

    assert migrated.revision_migration_version == 5
    assert migrated.revision_issues[0].completion_criteria == [
        "The plan records the choices that later execution code must implement."
    ]


def test_interrupted_approved_revision_plan_is_retryable_without_new_iteration(tmp_path: Path) -> None:
    orchestrator, project_id = _revision_ready_project(tmp_path)
    project = orchestrator.get_project(project_id)
    plan = project.approved_revision_plans[-1]
    plan.status = "in_progress"
    plan.target_batches[0].status = "in_progress"
    plan.target_batches[0].job_id = "failed_job"
    project.phase = ResearchPhase.HUMAN_REVISION_REVIEW
    project.active_job_id = None
    iteration = project.iteration
    orchestrator.store.save(project)

    recovered = orchestrator.get_project(project_id)

    assert recovered.phase == ResearchPhase.REVISION
    assert recovered.iteration == iteration
    assert recovered.active_revision_plan_id == plan.revision_plan_id
    assert recovered.approved_revision_plans[-1].status == "approved"
    assert recovered.approved_revision_plans[-1].target_batches[0].status == "pending"
    assert recovered.current_revision_action is None


def test_stale_revision_issue_ids_restore_immutable_batch_context(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Stale revision ids", planning_only=True)
    issue = RevisionIssue(
        classification="plan_blocking",
        target="analysis_plan",
        problem="Specify REML.",
        completion_criteria=["Estimator and corrections are fixed in protocol"],
    )
    project.revision_issues = [issue]
    batch = RevisionTargetBatch(
        target="analysis_plan",
        issue_ids=["revision_issue_old_id"],
        status="needs_attention",
    )
    plan = ApprovedRevisionPlan(
        project_id=project.project_id,
        review_version=1,
        revision_cycle=1,
        target_batches=[batch],
        status="needs_attention",
    )
    project.approved_revision_plans = [plan]
    project.active_revision_plan_id = plan.revision_plan_id
    project.phase = ResearchPhase.HUMAN_REVISION_REVIEW
    orchestrator.store.save(project)

    recovered = orchestrator.get_project(project.project_id)
    recovered_batch = recovered.approved_revision_plans[-1].target_batches[0]

    assert recovered.phase == ResearchPhase.REVISION
    assert recovered.approved_revision_plans[-1].status == "approved"
    assert recovered_batch.status == "pending"
    assert recovered_batch.issue_snapshots[0].issue_id == issue.issue_id
    assert recovered_batch.completion_criteria == [
        "Estimator and corrections are fixed in protocol"
    ]


def test_advisory_deterministic_check_does_not_override_independent_pass() -> None:
    criterion = "Covariates are listed"
    deterministic = [
        RevisionCriterionResult(
            criterion=criterion,
            passed=False,
            note="Advisory semantic token check; independent verification is authoritative.",
        )
    ]
    independent = [
        RevisionCriterionResult(
            criterion=criterion,
            passed=True,
            evidence="baseline BP, medication changes, weight changes",
            note="Concrete covariates are present.",
        )
    ]
    combined = combine_revision_verification_results(deterministic, independent)
    assert combined[0].passed is True


def test_hard_deterministic_check_still_blocks_independent_false_positive() -> None:
    criterion = "Estimator and corrections are fixed in protocol"
    deterministic = [
        RevisionCriterionResult(
            criterion=criterion,
            passed=False,
            note="Missing semantic groups: continuity correction",
        )
    ]
    independent = [RevisionCriterionResult(criterion=criterion, passed=True)]
    combined = combine_revision_verification_results(deterministic, independent)
    assert combined[0].passed is False


def _completed_verified_revision(orchestrator: ResearchOrchestrator):
    project = orchestrator.create_project("Bounded critical review", planning_only=True)
    verification = RevisionVerificationResult(
        action_id="batch",
        target_artifact="analysis_plan",
        artifact_version=2,
        criteria_results=[RevisionCriterionResult(criterion="criterion", passed=True)],
        overall_passed=True,
        verification_method="test",
    )
    batch = RevisionTargetBatch(
        target="analysis_plan",
        status="completed",
        verification_id=verification.verification_id,
    )
    project.revision_verifications = [verification]
    project.approved_revision_plans = [
        ApprovedRevisionPlan(
            project_id=project.project_id,
            review_version=1,
            revision_cycle=1,
            target_batches=[batch],
            status="completed",
        )
    ]
    return project


def test_verified_revision_rereview_converges_new_method_suggestions_to_non_blocking(
    tmp_path: Path,
) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = _completed_verified_revision(orchestrator)
    review = ReviewResult(
        evidence_quality_score=8,
        methodological_validity_score=7,
        feasibility_score=7,
        reproducibility_score=5,
        claim_support_score=8,
        uncertainty_handling_score=8,
        blocking_issues=["Add another optional sensitivity analysis."],
        decision="revise_analysis",
        failed_quality_gates=["reviewer_score_below_6", "blocking_issues_present"],
        required_revision_target="analysis",
    )

    converged = orchestrator._converge_verified_revision_review(project, review)

    assert converged.decision == "approve"
    assert converged.blocking_issues == []
    assert converged.failed_quality_gates == []
    assert converged.reproducibility_score == 6
    assert "Add another optional sensitivity analysis." in converged.non_blocking_issues


def test_verified_revision_does_not_override_integrity_blocker(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = _completed_verified_revision(orchestrator)
    review = ReviewResult(
        evidence_quality_score=8,
        methodological_validity_score=8,
        feasibility_score=8,
        reproducibility_score=8,
        claim_support_score=8,
        uncertainty_handling_score=8,
        blocking_issues=["A fabricated source was detected in the revised artifact."],
        decision="revise_evidence",
        required_revision_target="evidence",
    )

    converged = orchestrator._converge_verified_revision_review(project, review)

    assert converged.decision == "revise_evidence"
    assert converged.blocking_issues == ["A fabricated source was detected in the revised artifact."]


def _revision_ready_project(tmp_path: Path) -> tuple[ResearchOrchestrator, str]:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Execute revision")
    project.phase = ResearchPhase.HUMAN_REVISION_REVIEW
    project.analysis_plan = AnalysisPlan(statistical_methods=["DerSimonian-Laird"])
    project.revision_issues = [
        RevisionIssue(
            classification="plan_blocking",
            target="analysis_plan",
            problem="Specify REML.",
            completion_criteria=["Estimator and corrections are fixed in protocol"],
        )
    ]
    orchestrator.store.save(project)
    orchestrator.submit_revision_review(
        project.project_id,
        [RevisionIssueDecision(issue_id=project.revision_issues[0].issue_id, disposition="accept_ai")],
    )
    orchestrator._run_revision_model = lambda *args: AgentRun(  # type: ignore[method-assign]
        output=AnalysisPlan(statistical_methods=["REML with continuity correction"]),
        metadata=metadata(),
    )
    return orchestrator, project.project_id


def test_model_output_does_not_complete_action_when_verification_fails(tmp_path: Path) -> None:
    orchestrator, project_id = _revision_ready_project(tmp_path)
    failed = RevisionVerificationResult(
        action_id="batch",
        target_artifact="analysis_plan",
        artifact_version=2,
        criteria_results=[RevisionCriterionResult(criterion="criterion", passed=False)],
        overall_passed=False,
        verification_method="test",
    )
    orchestrator._verify_revision_batch = lambda *args: (failed, "verification-artifact")  # type: ignore[method-assign]
    result = orchestrator.run_next_step(project_id, job_id="job_test")
    project = orchestrator.get_project(project_id)
    assert result["current_phase"] == ResearchPhase.HUMAN_REVISION_REVIEW.value
    assert result["revision_required"] is True
    assert project.completed_revision_actions == []
    assert project.approved_revision_plans[-1].status == "needs_attention"


def test_evidence_revision_returns_to_auditable_research_instead_of_dead_end(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Verify a public dataset")
    project.phase = ResearchPhase.HUMAN_REVISION_REVIEW
    project.revision_issues = [
        RevisionIssue(
            classification="plan_blocking",
            target="evidence",
            problem="The dataset source and parsed fields must be verified.",
            completion_criteria=["A primary dataset source and field mapping are recorded."],
        )
    ]
    orchestrator.store.save(project)
    orchestrator.submit_revision_review(
        project.project_id,
        [RevisionIssueDecision(issue_id=project.revision_issues[0].issue_id, disposition="provide_content", instruction="Use the uploaded public dataset.")],
    )

    result = orchestrator.run_next_step(project.project_id, job_id="job_evidence")
    recovered = orchestrator.get_project(project.project_id)

    assert result["current_phase"] == ResearchPhase.BACKGROUND_RESEARCH.value
    assert recovered.phase == ResearchPhase.BACKGROUND_RESEARCH
    assert recovered.active_revision_plan_id is None
    assert recovered.current_revision_action is None
    assert "证据不能由自动修订器生成" in recovered.revision_recovery_messages[-1]


def test_failed_evidence_revision_can_be_resumed_without_spending_another_iteration(
    tmp_path: Path,
) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Verify a public dataset")
    project.phase = ResearchPhase.HUMAN_REVISION_REVIEW
    project.revision_issues = [
        RevisionIssue(
            classification="plan_blocking",
            target="evidence",
            problem="The dataset source must be verified.",
            completion_criteria=["A primary dataset is recorded."],
        )
    ]
    orchestrator.store.save(project)
    submitted = orchestrator.submit_revision_review(
        project.project_id,
        [
            RevisionIssueDecision(
                issue_id=project.revision_issues[0].issue_id,
                disposition="provide_content",
                instruction="Use the uploaded dataset.",
            )
        ],
    )
    active_plan = submitted.approved_revision_plans[-1]
    active_plan.status = "needs_attention"
    active_plan.target_batches[0].status = "needs_attention"
    submitted.phase = ResearchPhase.HUMAN_REVISION_REVIEW
    submitted.iteration = submitted.max_iterations
    orchestrator.store.save(submitted)

    recovered = orchestrator.resume_evidence_research(project.project_id)

    assert recovered.phase == ResearchPhase.BACKGROUND_RESEARCH
    assert recovered.active_revision_plan_id is None
    assert recovered.iteration == recovered.max_iterations
    assert "证据不能由自动修订器生成" in recovered.revision_recovery_messages[-1]


def test_failed_verification_triggers_targeted_auto_repair_before_human_review(tmp_path: Path) -> None:
    orchestrator, project_id = _revision_ready_project(tmp_path)
    model_calls: list[RevisionVerificationResult | None] = []

    def run_revision(*args):
        previous = args[4]
        model_calls.append(previous)
        return AgentRun(
            output=AnalysisPlan(statistical_methods=["REML with continuity correction"]),
            metadata=metadata(),
        )

    verification_calls = 0

    def verify_revision(*args):
        nonlocal verification_calls
        verification_calls += 1
        passed = verification_calls == 2
        result = RevisionVerificationResult(
            action_id="batch",
            target_artifact="analysis_plan",
            artifact_version=verification_calls + 1,
            criteria_results=[
                RevisionCriterionResult(
                    criterion="Estimator and corrections are fixed in protocol",
                    passed=passed,
                    note="passed" if passed else "continuity correction missing",
                )
            ],
            overall_passed=passed,
            verification_method="test",
        )
        return result, f"verification-{verification_calls}"

    orchestrator._run_revision_model = run_revision  # type: ignore[method-assign]
    orchestrator._verify_revision_batch = verify_revision  # type: ignore[method-assign]
    result = orchestrator.run_next_step(project_id, job_id="job_test")
    project = orchestrator.get_project(project_id)

    assert len(model_calls) == 2
    assert model_calls[0] is None
    assert model_calls[1] is not None
    assert result["current_phase"] == ResearchPhase.CRITICAL_REVIEW.value
    assert project.approved_revision_plans[-1].status == "completed"


def test_verified_batch_completes_then_enters_single_rereview(tmp_path: Path) -> None:
    orchestrator, project_id = _revision_ready_project(tmp_path)
    passed = RevisionVerificationResult(
        action_id="batch",
        target_artifact="analysis_plan",
        artifact_version=2,
        criteria_results=[RevisionCriterionResult(criterion="criterion", passed=True)],
        overall_passed=True,
        verification_method="test",
    )
    orchestrator._verify_revision_batch = lambda *args: (passed, "verification-artifact")  # type: ignore[method-assign]
    result = orchestrator.run_next_step(project_id, job_id="job_test")
    project = orchestrator.get_project(project_id)
    assert result["current_phase"] == ResearchPhase.CRITICAL_REVIEW.value
    assert len(project.completed_revision_actions) == 1
    assert project.iteration == 1


def test_revision_artifact_events_include_batch_and_version(tmp_path: Path) -> None:
    orchestrator, project_id = _revision_ready_project(tmp_path)
    project = orchestrator.get_project(project_id)
    plan = project.approved_revision_plans[-1]
    batch = plan.target_batches[0]
    run = AgentRun(output=AnalysisPlan(statistical_methods=["REML"]), metadata=metadata())
    project.active_job_id = "job_test"
    orchestrator._record_revision_output(project, plan, batch, run)
    orchestrator._record_revision_output(project, plan, batch, run)
    events = orchestrator.list_events(project_id)
    keys = [event.display_key for event in events if event.display_key and "generated" in event.display_key]
    assert len(keys) == 2
    assert keys[0] != keys[1]
    assert any("v1_generated" in key for key in keys)
    assert any("v2_generated" in key for key in keys)


def test_planning_only_execution_prerequisite_does_not_block_approval(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Planning only", planning_only=True)
    review = ReviewResult(
        evidence_quality_score=8,
        methodological_validity_score=8,
        feasibility_score=8,
        reproducibility_score=8,
        claim_support_score=8,
        uncertainty_handling_score=8,
        blocking_issues=["Register PROSPERO"],
        decision="revise_method",
        required_revision_target="method",
        revision_plan=[RevisionAction(target="method", reason="Register PROSPERO", required_changes=["Register PROSPERO"])],
    )
    prepared = orchestrator._prepare_review_for_project(project, review)
    assert prepared.decision == "approve"
    assert prepared.blocking_issues == []
    assert project.execution_requirements == ["Register PROSPERO"]


def test_frontend_contains_human_gate_and_specific_submit_error() -> None:
    source = Path("app_streamlit.py").read_text(encoding="utf-8")
    assert "独立审查要求修订" in source
    assert "修订提交失败" in source
    assert '"HUMAN_REVISION_REVIEW"' in source


def test_revision_review_api_routes_are_registered() -> None:
    source = Path("src/main_api.py").read_text(encoding="utf-8")
    assert '/revision-review")' in source
    assert '/revision-review/submit")' in source
    assert '/revision-review/defer")' in source
    assert '/revision-review/cancel")' in source
