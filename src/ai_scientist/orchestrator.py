"""Sequential orchestration for structured, auditable research planning."""

from __future__ import annotations

import os
import time
import traceback
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from src.ai_scientist.agents import (
    AnalystAgent,
    EvidenceResearcherAgent,
    HypothesisScientistAgent,
    MethodologistAgent,
    ReproducibilityEngineerAgent,
    ResearchDirectorAgent,
    ScientificSynthesizerAgent,
    SkepticalReviewerAgent,
    StudyDesignerAgent,
)
from src.ai_scientist.agents.base_agent import AgentRun
from src.ai_scientist.artifact_store import ArtifactStore
from src.ai_scientist.claim_graph import ClaimGraph
from src.ai_scientist.domain_resolution import resolve_domain
from src.ai_scientist.domain_router import DomainRouter
from src.ai_scientist.events import completed_event
from src.ai_scientist.evidence_verifier import verify_evidence_collection
from src.ai_scientist.exceptions import (
    AIScientistError,
    BudgetExceededError,
    InvalidEvidenceReferenceError,
    InvalidTransitionError,
)
from src.ai_scientist.method_selector import MethodSelector
from src.ai_scientist.model_registry import ModelRegistry
from src.ai_scientist.presentation import (
    render_analysis_plan,
    render_claim_mapping,
    render_domain_selection,
    render_evidence_summary,
    render_feasibility_review,
    render_hypotheses,
    render_method_selection,
    render_research_question,
    render_study_design,
    render_synthesis,
)
from src.ai_scientist.project_store import ProjectStore
from src.ai_scientist.quality import apply_reviewer_quality_gates, compute_quality_metrics, enrich_evidence_items
from src.ai_scientist.report_writer import build_research_plan_json, build_research_plan_markdown
from src.ai_scientist.schemas import (
    AnalysisPlan,
    Claim,
    ClaimEvidenceMappingResult,
    DomainSelectionOutput,
    EvidenceCollection,
    EvidenceResearchOutput,
    EvidenceItem,
    SearchAcquisitionResult,
    SearchCandidate,
    SearchPlan,
    SearchQueryRecord,
    Hypothesis,
    MethodologyOutput,
    ResearchEvent,
    ResearchPhase,
    ResearchQuestion,
    ResearchProject,
    ReviewResult,
    RevisionAction,
    StudyDesign,
    new_id,
    utc_now,
)
from src.ai_scientist.state_machine import ResearchStateMachine, TERMINAL_PHASES
from src.ai_scientist.stagnation_detector import build_revision_snapshot, detect_stagnation
from src.ai_scientist.source_selector import select_sources
from src.ai_scientist.structured_client import StructuredQwenClient, StructuredCallMetadata
from src.ai_scientist.tools.execution_adapter import ExecutionAdapter
from src.ai_scientist.tools.registry import ToolRegistry
from src.model_utils import normalize_model_overrides


_CURRENT_JOB_ID: ContextVar[str | None] = ContextVar("current_research_job_id", default=None)


class ResearchOrchestrator:
    """Advance exactly one explicit research phase per call."""

    def __init__(self, projects_dir: str | Path | None = None) -> None:
        self.store = ProjectStore(projects_dir)
        self.artifacts = ArtifactStore(self.store.root)
        self.state_machine = ResearchStateMachine()
        self.method_selector = MethodSelector()
        self.domain_router = DomainRouter()
        self.tools = ToolRegistry()
        self.execution = ExecutionAdapter()

    def create_project(
        self,
        objective: str,
        domain_hint: str | None = None,
        constraints_text: str = "",
        constraints: dict[str, Any] | None = None,
        model_overrides: dict[str, str | None] | None = None,
        max_iterations: int = 2,
        planning_only: bool = True,
    ) -> ResearchProject:
        max_calls = int(os.getenv("AI_SCIENTIST_MAX_MODEL_CALLS", "50"))
        project = ResearchProject(
            title=objective.strip()[:100] or "Untitled research project",
            objective=objective.strip(),
            domain_hint=domain_hint,
            constraints={**(constraints or {}), **({"constraints_text": constraints_text} if constraints_text else {})},
            model_overrides=normalize_model_overrides(model_overrides),
            max_iterations=max_iterations,
            planning_only=planning_only,
            budget={
                "max_model_calls": max_calls,
                "max_iterations": max_iterations,
            },
            available_tools=self.tools.available_names(),
            missing_capabilities=self.tools.unavailable_names(),
        )
        self.store.save(project)
        event = completed_event(
            project.project_id,
            ResearchPhase.INTAKE,
            "orchestrator",
            status="created",
        )
        self._append_event(project, event)
        return project

    def run_next_step(self, project_id: str, job_id: str | None = None) -> dict[str, Any]:
        project = self.get_project(project_id)
        previous_phase = project.phase
        if project.phase in TERMINAL_PHASES:
            raise InvalidTransitionError(f"Project in terminal phase {project.phase.value} cannot continue.")
        produced_artifacts: list[str] = []
        review_decision: str | None = None
        revision_required = False
        blocking_issues: list[str] = []
        max_revision_exhausted = False
        stage_status = "completed"
        started_event = ResearchEvent(
            job_id=job_id,
            project_id=project.project_id,
            phase=previous_phase,
            agent_name=self._agent_name_for_phase(previous_phase),
            status="running",
        )
        self._append_event(project, started_event)
        token = _CURRENT_JOB_ID.set(job_id)
        try:
            if project.phase == ResearchPhase.INTAKE:
                self._transition_event(project, "next")
            elif project.phase == ResearchPhase.QUESTION_FORMULATION:
                run = self._run_agent(project, ResearchDirectorAgent)
                project.question = run.output.research_question
                project.title = run.output.project_title
                project.constraints.update(run.output.initial_constraints)
                project.human_actions_required = run.output.clarification_questions
                produced_artifacts += self._record_agent_output(project, previous_phase, run, "research_question")
                self.state_machine.transition(project, "next")
            elif project.phase == ResearchPhase.RESEARCH_MODE_SELECTION:
                if project.question is None:
                    raise AIScientistError("Research mode selection requires a formulated question.")
                run = self._run_agent(project, MethodologistAgent)
                project.research_mode = run.output.selected_research_mode
                project.method_rationale = run.output.methodological_rationale
                project.validity_threats = run.output.validity_threats
                project.required_controls = run.output.required_controls
                project.human_actions_required = run.output.human_actions_required
                produced_artifacts += self._record_agent_output(project, previous_phase, run, "research_mode_selection")
                self.state_machine.transition(project, "next")
            elif project.phase == ResearchPhase.DOMAIN_SELECTION:
                if project.question is None:
                    raise AIScientistError("Domain selection requires a formulated question.")
                selection, metadata = self._run_domain_selection(project)
                resolution = resolve_domain(selection.primary_domain, selection.secondary_domains)
                project.domain_resolution = resolution
                project.domain = resolution.canonical_primary_domain
                project.secondary_domains = resolution.canonical_secondary_domains
                project.human_actions_required.extend(selection.clarification_questions)
                produced_artifacts += self._record_structured_output(
                    project,
                    previous_phase,
                    "research_director",
                    {
                        "selection": selection.model_dump(mode="json"),
                        "domain_resolution": resolution.model_dump(mode="json"),
                    },
                    "domain_selection",
                    metadata=metadata,
                    display_markdown=render_domain_selection(project.domain, project.secondary_domains),
                )
                self.state_machine.transition(project, "next")
            elif project.phase == ResearchPhase.BACKGROUND_RESEARCH:
                produced_artifacts += self._run_background_research(project, previous_phase)
                self.state_machine.transition(project, "next")
            elif project.phase == ResearchPhase.CLAIM_EVIDENCE_MAPPING:
                produced_artifacts += self._execute_claim_evidence_mapping(project, previous_phase)
            elif project.phase == ResearchPhase.HYPOTHESIS_GENERATION:
                run = self._run_agent(project, HypothesisScientistAgent)
                project.hypotheses = run.output.hypotheses
                self._refresh_quality_metrics(project)
                produced_artifacts += self._record_agent_output(project, previous_phase, run, "hypotheses")
                self.state_machine.transition(project, "next")
            elif project.phase == ResearchPhase.METHOD_SELECTION:
                run = self._run_agent(project, MethodologistAgent)
                project.research_mode = run.output.selected_research_mode
                project.method_rationale = run.output.methodological_rationale
                project.validity_threats = run.output.validity_threats
                project.required_controls = run.output.required_controls
                project.human_actions_required.extend(run.output.human_actions_required)
                produced_artifacts += self._record_agent_output(project, previous_phase, run, "methodology")
                self.state_machine.transition(project, "next")
            elif project.phase == ResearchPhase.STUDY_DESIGN:
                run = self._run_agent(project, StudyDesignerAgent)
                project.study_design = run.output
                project.human_actions_required.extend(run.output.human_actions_required)
                produced_artifacts += self._record_agent_output(project, previous_phase, run, "study_design")
                self.state_machine.transition(project, "next")
            elif project.phase == ResearchPhase.ANALYSIS_PLANNING:
                run = self._run_agent(project, AnalystAgent)
                project.analysis_plan = run.output
                produced_artifacts += self._record_agent_output(project, previous_phase, run, "analysis_plan")
                self.state_machine.transition(project, "next")
            elif project.phase == ResearchPhase.FEASIBILITY_REVIEW:
                reproduction = self._run_agent(project, ReproducibilityEngineerAgent)
                project.reproducibility_plan = reproduction.output.model_dump(mode="json")
                produced_artifacts += self._record_agent_output(
                    project, previous_phase, reproduction, "reproducibility_plan"
                )
                review = self._run_agent(project, SkepticalReviewerAgent)
                self._refresh_quality_metrics(project)
                gated_review = apply_reviewer_quality_gates(review.output, project.quality_metrics)
                project.reviews.append(gated_review)
                self._refresh_quality_metrics(project)
                project.human_actions_required.extend(gated_review.blocking_issues)
                review = AgentRun(
                    output=gated_review,
                    metadata=review.metadata,
                    tool_names=review.tool_names,
                    auxiliary=review.auxiliary,
                )
                produced_artifacts += self._record_agent_output(project, previous_phase, review, "independent_review")
                review_decision = review.output.decision
                blocking_issues = review.output.blocking_issues
                review_flow = self._apply_review_decision(project, review.output)
                revision_required = review_flow["revision_required"]
                max_revision_exhausted = review_flow["max_revision_exhausted"]
            elif project.phase == ResearchPhase.HUMAN_APPROVAL:
                stage_status = "awaiting_human_approval"
            elif project.phase == ResearchPhase.HUMAN_INTERVENTION_REQUIRED:
                stage_status = "waiting_for_human_intervention"
            elif project.phase == ResearchPhase.REVISION:
                target = project.pending_revision_target
                if not target:
                    raise AIScientistError("Revision target is missing.")
                self._transition_event(project, target)
                project.pending_revision_target = None
            elif project.phase == ResearchPhase.EXECUTION_WAITING:
                if project.planning_only:
                    self._transition_event(project, "planning_only")
                else:
                    stage_status = "waiting_for_execution_or_data"
                    project.human_actions_required.append(
                        "Provide data or connect an approved execution backend; no results were generated."
                    )
            elif project.phase == ResearchPhase.DATA_ANALYSIS:
                stage_status = "waiting_for_analysis_backend"
                project.human_actions_required.append(
                    "A dataset is registered, but no analysis backend is connected; no results were generated."
                )
            elif project.phase == ResearchPhase.CRITICAL_REVIEW:
                review = self._run_agent(project, SkepticalReviewerAgent)
                project.reviews.append(review.output)
                produced_artifacts += self._record_agent_output(project, previous_phase, review, "critical_review")
                review_decision = review.output.decision
                blocking_issues = review.output.blocking_issues
                review_flow = self._apply_review_decision(project, review.output)
                revision_required = review_flow["revision_required"]
                max_revision_exhausted = review_flow["max_revision_exhausted"]
            elif project.phase == ResearchPhase.SYNTHESIS:
                run = self._run_agent(project, ScientificSynthesizerAgent)
                project.conclusion = run.output
                self._refresh_quality_metrics(project)
                produced_artifacts += self._record_agent_output(project, previous_phase, run, "research_plan_synthesis")
                produced_artifacts += self._write_research_plan(project)
                self.state_machine.transition(project, "next")
            else:
                raise InvalidTransitionError(f"No stage handler for phase {project.phase.value}")
            self._advance_revision_queue_after_phase(project, previous_phase)
            self.store.save(project)
        except Exception as exc:
            stage_substep = self._substep_for_error(previous_phase, exc)
            cause_type = getattr(exc, "cause_type", None) or type(exc).__name__
            cause_message = getattr(exc, "cause_message", None) or str(exc)
            event = ResearchEvent(
                job_id=job_id,
                project_id=project.project_id,
                phase=previous_phase,
                agent_name=self._agent_name_for_phase(previous_phase),
                status="failed",
                error=self._sanitize_error(exc),
                error_type=type(exc).__name__,
                error_message=self._sanitize_error(exc),
                schema_valid=False,
                finished_at=utc_now(),
                failing_component=getattr(exc, "failing_component", None)
                or self._failing_component_for_substep(stage_substep),
                failure_category=getattr(exc, "failure_category", None),
                artifact_type=getattr(exc, "artifact_type", None),
                stage_substep=stage_substep,
                requested_model=getattr(exc, "requested_model", None),
                actual_model=getattr(exc, "actual_model", None),
                attempted_model=self._agent_name_for_phase(previous_phase),
                fallback_attempted=True,
                tool_name="web_search" if previous_phase == ResearchPhase.BACKGROUND_RESEARCH else None,
                tool_names=getattr(exc, "tool_names", []),
                safe_traceback=self._sanitize_traceback(exc),
                display_markdown=self._failure_display_message(previous_phase, stage_substep),
                validation_errors=getattr(exc, "validation_errors", None) or self._validation_errors(exc),
                attempted_calls=1 if previous_phase == ResearchPhase.BACKGROUND_RESEARCH else 0,
                failed_calls=1,
                status_code=getattr(exc, "status_code", None),
                provider_error_code=getattr(exc, "provider_error_code", None),
                provider_error_message=getattr(exc, "provider_error_message", None),
                request_id=getattr(exc, "request_id", None),
                endpoint_host=getattr(exc, "endpoint_host", None),
                previous_response_id_present=getattr(exc, "previous_response_id_present", None),
            )
            event.provider_error_code = event.provider_error_code or cause_type
            event.provider_error_message = event.provider_error_message or self._sanitize_text(cause_message)
            project.stage_messages.append(event.display_markdown)
            self._append_event(project, event)
            project.phase = previous_phase
            self.store.save(project)
            raise
        finally:
            _CURRENT_JOB_ID.reset(token)
        return {
            "project_id": project.project_id,
            "previous_phase": previous_phase.value,
            "current_phase": project.phase.value,
            "stage_status": stage_status,
            "produced_artifacts": produced_artifacts,
            "human_actions_required": list(dict.fromkeys(project.human_actions_required)),
            "review_decision": review_decision,
            "revision_required": revision_required,
            "blocking_issues": blocking_issues,
            "iteration": project.iteration,
            "max_revision_exhausted": max_revision_exhausted,
        }

    def approve_project(self, project_id: str) -> ResearchProject:
        project = self.get_project(project_id)
        if project.phase != ResearchPhase.HUMAN_APPROVAL:
            raise InvalidTransitionError("Project can only be approved from HUMAN_APPROVAL.")
        self._transition_event(project, "approve")
        if project.planning_only and project.phase == ResearchPhase.EXECUTION_WAITING:
            self._transition_event(project, "planning_only")
        self.store.save(project)
        return project

    def request_revision(self, project_id: str, target_phase: str, feedback: str) -> ResearchProject:
        project = self.get_project(project_id)
        if project.phase != ResearchPhase.HUMAN_APPROVAL:
            raise InvalidTransitionError("Revision can only be requested from HUMAN_APPROVAL.")
        if target_phase not in {"question", "evidence", "hypothesis", "method", "design", "analysis"}:
            raise ValueError(f"Unsupported revision target: {target_phase}")
        project.revision_feedback.append(feedback)
        project.pending_revision_target = target_phase
        self._targeted_rollback(project, target_phase, feedback, changed_fields=[])
        self.store.save(project)
        return project

    def patch_question(self, project_id: str, patch: dict[str, Any], reason: str = "") -> ResearchProject:
        project = self.get_project(project_id)
        if project.question is None:
            raise AIScientistError("No research question exists to edit.")
        project.question = ResearchQuestion.model_validate({**project.question.model_dump(mode="json"), **patch})
        self._record_human_edit(project, "question", patch, reason)
        self._targeted_rollback(project, "question", reason, list(patch))
        self.store.save(project)
        return project

    def patch_hypothesis(
        self,
        project_id: str,
        hypothesis_id: str,
        patch: dict[str, Any],
        reason: str = "",
    ) -> ResearchProject:
        project = self.get_project(project_id)
        for index, hypothesis in enumerate(project.hypotheses):
            if hypothesis.hypothesis_id == hypothesis_id:
                project.hypotheses[index] = Hypothesis.model_validate(
                    {**hypothesis.model_dump(mode="json"), **patch}
                )
                self._record_human_edit(project, "hypotheses", patch, reason)
                self._targeted_rollback(project, "hypothesis", reason, list(patch))
                self.store.save(project)
                return project
        raise AIScientistError(f"Hypothesis not found: {hypothesis_id}")

    def patch_study_design(self, project_id: str, patch: dict[str, Any], reason: str = "") -> ResearchProject:
        project = self.get_project(project_id)
        if project.study_design is None:
            raise AIScientistError("No study design exists to edit.")
        project.study_design = StudyDesign.model_validate({**project.study_design.model_dump(mode="json"), **patch})
        self._record_human_edit(project, "study_design", patch, reason)
        self._targeted_rollback(project, "design", reason, list(patch))
        self.store.save(project)
        return project

    def patch_analysis_plan(self, project_id: str, patch: dict[str, Any], reason: str = "") -> ResearchProject:
        project = self.get_project(project_id)
        if project.analysis_plan is None:
            raise AIScientistError("No analysis plan exists to edit.")
        project.analysis_plan = AnalysisPlan.model_validate({**project.analysis_plan.model_dump(mode="json"), **patch})
        self._record_human_edit(project, "analysis_plan", patch, reason)
        self._targeted_rollback(project, "analysis", reason, list(patch))
        self.store.save(project)
        return project

    def add_evidence(self, project_id: str, evidence: EvidenceItem, reason: str = "") -> ResearchProject:
        project = self.get_project(project_id)
        project.evidence = enrich_evidence_items(project.evidence + [evidence])
        self._refresh_quality_metrics(project)
        self._record_human_edit(project, "evidence", evidence.model_dump(mode="json"), reason)
        self._targeted_rollback(project, "evidence", reason, ["evidence"])
        self.store.save(project)
        return project

    def delete_evidence(self, project_id: str, evidence_id: str, reason: str = "") -> ResearchProject:
        project = self.get_project(project_id)
        before = len(project.evidence)
        project.evidence = [item for item in project.evidence if item.evidence_id != evidence_id]
        if len(project.evidence) == before:
            raise AIScientistError(f"Evidence not found: {evidence_id}")
        for claim in project.claims:
            claim.supporting_evidence_ids = [item for item in claim.supporting_evidence_ids if item != evidence_id]
            claim.contradicting_evidence_ids = [item for item in claim.contradicting_evidence_ids if item != evidence_id]
        self._refresh_quality_metrics(project)
        self._record_human_edit(project, "evidence", {"deleted_evidence_id": evidence_id}, reason)
        self._targeted_rollback(project, "evidence", reason, ["evidence"])
        self.store.save(project)
        return project

    def provide_data(
        self,
        project_id: str,
        artifact_paths: list[str],
        description: str,
        data_type: str,
    ) -> ResearchProject:
        project = self.get_project(project_id)
        record = self.artifacts.save_json(
            project.project_id,
            "provided_data_manifest",
            {"artifact_paths": artifact_paths, "description": description, "data_type": data_type},
            "human",
        )
        project.artifacts.append(record)
        if project.phase == ResearchPhase.EXECUTION_WAITING:
            self._transition_event(project, "data_provided")
        self.store.save(project)
        return project

    def mark_execution_complete(self, project_id: str, result_artifact_ids: list[str]) -> ResearchProject:
        project = self.get_project(project_id)
        if project.phase != ResearchPhase.EXECUTION:
            raise InvalidTransitionError("Execution can only be completed from EXECUTION.")
        known = {item.artifact_id for item in project.artifacts}
        missing = set(result_artifact_ids) - known
        if missing:
            raise ValueError(f"Unknown result artifacts: {sorted(missing)}")
        self._transition_event(project, "success")
        self.store.save(project)
        return project

    def cancel_project(self, project_id: str) -> ResearchProject:
        project = self.get_project(project_id)
        if project.phase in TERMINAL_PHASES:
            raise InvalidTransitionError(f"Project is already terminal: {project.phase.value}")
        previous = project.phase
        project.phase = ResearchPhase.CANCELLED
        event = completed_event(project.project_id, previous, "human", status="cancelled")
        self._append_event(project, event)
        self.store.save(project)
        return project

    def get_project(self, project_id: str) -> ResearchProject:
        return self.store.load(project_id)

    def list_events(self, project_id: str) -> list[ResearchEvent]:
        self.get_project(project_id)
        return self.store.list_events(project_id)

    def list_artifacts(self, project_id: str) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in self.get_project(project_id).artifacts]

    def capabilities(self, project_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        return {
            **self.tools.capabilities(),
            "execution_capabilities": self.execution.capabilities(),
            "human_actions_required": project.human_actions_required,
        }

    def _apply_review_decision(self, project: ResearchProject, review: ReviewResult) -> dict[str, Any]:
        if review.decision == "approve":
            self.state_machine.transition(project, "approve")
            return {"revision_required": False, "max_revision_exhausted": False}
        if review.decision == "reject":
            self.state_machine.transition(project, "reject")
            return {"revision_required": False, "max_revision_exhausted": False}

        actions = self._revision_actions_from_review(review)
        project.pending_revision_actions = actions
        project.current_revision_action = None
        snapshot = build_revision_snapshot(project)
        project.revision_snapshots.append(snapshot)
        if detect_stagnation(project):
            latest = project.revision_snapshots[-1]
            project.revision_snapshots[-1] = latest.model_copy(update={"stagnation_detected": True})
            project.quality_metrics = project.quality_metrics.model_copy(update={"stagnation_detected": True})
            project.phase = ResearchPhase.HUMAN_INTERVENTION_REQUIRED
            project.stage_messages.append(
                "系统连续两轮未能获得新的可验证证据或质量改善，自动修订已暂停。请上传论文、提供 DOI/PMID/正式链接、调整范围，或接受当前证据不足的结论。"
            )
            return {"revision_required": True, "max_revision_exhausted": False}
        if project.iteration >= project.max_iterations:
            project.phase = ResearchPhase.HUMAN_INTERVENTION_REQUIRED
            project.stage_messages.append(
                "自动修订已达到上限，但项目并非失败。当前需要人工提供来源、调整范围，或确认接受证据不足结论。"
            )
            return {"revision_required": True, "max_revision_exhausted": True}
        project.iteration += 1
        project.budget.used_iterations += 1
        self._start_next_revision_action(project)
        return {"revision_required": True, "max_revision_exhausted": False}

    def _revision_actions_from_review(self, review: ReviewResult) -> list[RevisionAction]:
        actions = list(review.revision_plan)
        if not actions and review.required_revision_target != "none":
            actions.append(
                RevisionAction(
                    target=self._normalize_revision_target(review.required_revision_target),
                    priority=1,
                    reason="Reviewer requested a targeted revision.",
                    required_changes=review.blocking_issues[:5],
                    completion_criteria=["The targeted revision is completed and re-reviewed."],
                )
            )
        if not actions:
            actions.append(
                RevisionAction(
                    target="evidence",
                    priority=1,
                    reason="Reviewer requested revision but did not provide a target.",
                    required_changes=review.blocking_issues[:5],
                    completion_criteria=["Reviewer blocking issues are addressed."],
                )
            )
        return sorted(
            [item.model_copy(update={"target": self._normalize_revision_target(item.target), "status": "pending"}) for item in actions],
            key=lambda item: item.priority,
        )

    def _start_next_revision_action(self, project: ResearchProject) -> None:
        if not project.pending_revision_actions:
            project.current_revision_action = None
            project.phase = ResearchPhase.CRITICAL_REVIEW
            return
        action = project.pending_revision_actions.pop(0).model_copy(update={"status": "in_progress"})
        project.current_revision_action = action
        project.phase = self._phase_for_revision_target(action.target)
        project.stage_messages.append(self._revision_action_message(action, len(project.pending_revision_actions) + 1))

    def _advance_revision_queue_after_phase(self, project: ResearchProject, completed_phase: ResearchPhase) -> None:
        action = project.current_revision_action
        if action is None:
            return
        if self._completion_phase_for_revision_target(action.target) != completed_phase:
            return
        project.completed_revision_actions.append(action.model_copy(update={"status": "completed"}))
        project.current_revision_action = None
        self._start_next_revision_action(project)

    @staticmethod
    def _normalize_revision_target(target: str) -> str:
        return {
            "design": "study_design",
            "analysis": "analysis_plan",
            "method": "method",
            "hypothesis": "hypothesis",
        }.get(target, target)

    @staticmethod
    def _phase_for_revision_target(target: str) -> ResearchPhase:
        return {
            "question": ResearchPhase.QUESTION_FORMULATION,
            "evidence": ResearchPhase.BACKGROUND_RESEARCH,
            "hypothesis": ResearchPhase.HYPOTHESIS_GENERATION,
            "method": ResearchPhase.METHOD_SELECTION,
            "study_design": ResearchPhase.STUDY_DESIGN,
            "analysis_plan": ResearchPhase.ANALYSIS_PLANNING,
            "reproducibility_plan": ResearchPhase.FEASIBILITY_REVIEW,
        }.get(target, ResearchPhase.BACKGROUND_RESEARCH)

    @staticmethod
    def _completion_phase_for_revision_target(target: str) -> ResearchPhase:
        return {
            "evidence": ResearchPhase.CLAIM_EVIDENCE_MAPPING,
            "question": ResearchPhase.QUESTION_FORMULATION,
            "hypothesis": ResearchPhase.HYPOTHESIS_GENERATION,
            "method": ResearchPhase.METHOD_SELECTION,
            "study_design": ResearchPhase.STUDY_DESIGN,
            "analysis_plan": ResearchPhase.ANALYSIS_PLANNING,
            "reproducibility_plan": ResearchPhase.FEASIBILITY_REVIEW,
        }.get(target, ResearchPhase.BACKGROUND_RESEARCH)

    @staticmethod
    def _revision_action_message(action: RevisionAction, remaining_count: int) -> str:
        target_label = {
            "evidence": "补充可验证证据",
            "reproducibility_plan": "完善研究与复现方案",
            "analysis_plan": "修订分析方案",
            "study_design": "修订研究设计",
            "method": "修订方法选择",
            "hypothesis": "修订假设",
            "question": "修订研究问题",
        }.get(action.target, action.target)
        suffix = f" 后续还有 {remaining_count - 1} 项修订。" if remaining_count > 1 else ""
        return f"独立审查要求定向修订：{target_label}。{action.reason}{suffix}"

    def debug_claim_mapping(self, project_id: str) -> dict[str, Any]:
        """Dry-run claim mapping validation without changing formal project state."""

        project = self.get_project(project_id).model_copy(deep=True)
        try:
            collection = self._latest_evidence_collection(project)
            self._validate_evidence_ids(collection)
            mapping, metadata = self._run_claim_mapping(project, collection, track_failure_budget=False)
            claims = self._canonicalize_claim_mapping(mapping, collection)
            graph = ClaimGraph(collection.evidence_items, claims)
            graph.validate_or_raise()
            graph_errors = graph.validate()
            if graph_errors:
                raise AIScientistError(
                    "Claim graph dry-run validation failed.",
                    stage=ResearchPhase.CLAIM_EVIDENCE_MAPPING.value,
                    substep="claim_graph_build",
                    cause_type="ClaimGraphValidationError",
                    cause_message="; ".join(graph_errors),
                    validation_errors=[{"msg": item} for item in graph_errors],
                    artifact_type="claim_graph",
                    failure_category="orchestration_postprocess_error",
                    failing_component="claim_graph_build",
                )
            unsupported = [item.claim_id for item in graph.find_unsupported_claims()]
            links = graph.to_dict()["links"]
            return {
                "status": "ok",
                "message": (
                    f"已读取 {len(collection.evidence_items)} 条证据，生成 {len(claims)} 条主张，"
                    f"建立 {len(links)} 条支持或反驳关系。其中 {len(unsupported)} 条主张缺少可靠证据。"
                ),
                "debug": {
                    "evidence_version": project.active_artifact_versions.get("evidence"),
                    "evidence_count": len(collection.evidence_items),
                    "claim_count": len(claims),
                    "link_count": len(links),
                    "invalid_reference_count": 0,
                    "schema_valid": True,
                    "requested_model": metadata.requested_model,
                    "actual_model": metadata.actual_model,
                    "failing_component": None,
                    "cause_type": None,
                    "cause_message": None,
                },
            }
        except Exception as exc:  # noqa: BLE001 - diagnostic endpoint returns safe details.
            return {
                "status": "error",
                "message": self._failure_display_message(
                    ResearchPhase.CLAIM_EVIDENCE_MAPPING,
                    self._substep_for_error(ResearchPhase.CLAIM_EVIDENCE_MAPPING, exc),
                ),
                "debug": {
                    "evidence_count": len(project.evidence),
                    "claim_count": len(project.claims),
                    "invalid_reference_count": 1 if isinstance(exc, InvalidEvidenceReferenceError) else 0,
                    "schema_valid": False,
                    "failing_component": getattr(exc, "failing_component", None)
                    or self._failing_component_for_substep(
                        self._substep_for_error(ResearchPhase.CLAIM_EVIDENCE_MAPPING, exc)
                    ),
                    "cause_type": getattr(exc, "cause_type", None) or type(exc).__name__,
                    "cause_message": self._sanitize_text(getattr(exc, "cause_message", None) or str(exc)),
                    "artifact_type": getattr(exc, "artifact_type", None),
                    "validation_errors": getattr(exc, "validation_errors", None) or self._validation_errors(exc),
                },
            }

    def _run_background_research(self, project: ResearchProject, phase: ResearchPhase) -> list[str]:
        if not hasattr(EvidenceResearcherAgent, "search_one_query"):
            return self._run_background_research_legacy(project, phase)
        produced: list[str] = []
        agent = EvidenceResearcherAgent(client=self._structured_client_for_project(project))
        resolution = project.domain_resolution or resolve_domain(project.domain, project.secondary_domains)
        project.domain_resolution = resolution
        project.domain = resolution.canonical_primary_domain
        project.secondary_domains = resolution.canonical_secondary_domains
        self._append_event(
            project,
            completed_event(
                project.project_id,
                phase,
                "evidence_researcher",
                status="skill_resolution_started",
                stage_substep="domain_skill_resolution",
            ),
        )
        self._append_event(
            project,
            completed_event(
                project.project_id,
                phase,
                "evidence_researcher",
                status="skill_resolution_completed",
                stage_substep="domain_skill_resolution",
                fallback_used=resolution.fallback_used,
                display_markdown=f"Domain skill resolved to {resolution.loaded_domain_skill}.",
            ),
        )

        checkpoint = project.background_research_checkpoint
        invocation_started = time.monotonic()
        elapsed_before_invocation = checkpoint.elapsed_seconds
        total_budget = float(os.getenv("AI_SCIENTIST_SEARCH_TOTAL_BUDGET", "600"))
        max_results = max(1, int(os.getenv("AI_SCIENTIST_MAX_SEARCH_RESULTS_PER_QUERY", "5")))
        max_sources = max(1, int(os.getenv("AI_SCIENTIST_MAX_EXTRACTED_SOURCES", "8")))
        min_usable = max(1, int(os.getenv("AI_SCIENTIST_MIN_USABLE_SOURCES", "3")))
        search_resolution = ModelRegistry(project.model_overrides).resolve_model("evidence_researcher")
        requested_search_model = os.getenv(
            "AI_SCIENTIST_SEARCH_ACQUISITION_MODEL", search_resolution.resolved_model
        ).strip() or search_resolution.resolved_model
        fallback_search_model = os.getenv("AI_SCIENTIST_SEARCH_FALLBACK_MODEL", "").strip()
        if checkpoint.started_at is None:
            checkpoint.started_at = utc_now()

        if checkpoint.search_plan is None:
            self._ensure_budget(project, 1)
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    phase,
                    "evidence_researcher",
                    status="search_plan_started",
                    stage_substep="search_planning",
                    visibility="user",
                    display_key="background_search_started",
                    summary_markdown="开始检索背景证据。",
                ),
            )
            try:
                plan_run = agent.plan_search(project)
            except Exception:
                project.budget.attempted_model_calls += 1
                project.budget.failed_model_calls += 1
                self.store.save(project)
                raise
            self._apply_call_metadata(project, plan_run.metadata)
            checkpoint.search_plan = plan_run.output
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    phase,
                    "evidence_researcher",
                    status="search_plan_completed",
                    stage_substep="search_planning",
                    query_count=len(plan_run.output.queries),
                    requested_model=plan_run.metadata.requested_model,
                    actual_model=plan_run.metadata.actual_model,
                    fallback_used=plan_run.metadata.fallback_used,
                ),
            )
            produced += self._save_search_checkpoint(project, checkpoint)

        records_by_query = {item.query: item for item in checkpoint.query_records}
        for query in checkpoint.search_plan.queries:
            if records_by_query.get(query) and records_by_query[query].status == "completed":
                continue
            if elapsed_before_invocation + (time.monotonic() - invocation_started) >= total_budget:
                break
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    phase,
                    "evidence_researcher",
                    status="search_query_started",
                    stage_substep="search_query",
                    requested_model=requested_search_model,
                    actual_model=requested_search_model,
                    tool_names=["web_search"],
                    tool_name="web_search",
                ),
            )
            started = time.monotonic()
            actual_model = requested_search_model
            fallback_reason = ""
            original_error = ""
            try:
                result = agent.search_one_query(query, requested_search_model)
                status = "completed"
            except Exception as exc:
                original_error = self._sanitize_text(str(exc))
                if self._is_timeout_error(exc) and fallback_search_model and fallback_search_model != requested_search_model:
                    actual_model = fallback_search_model
                    fallback_reason = "Primary search query timed out."
                    try:
                        result = agent.search_one_query(query, fallback_search_model)
                        status = "completed"
                    except Exception as fallback_exc:
                        original_error = self._sanitize_text(str(fallback_exc))
                        status = "timeout" if self._is_timeout_error(fallback_exc) else "failed"
                        result = {"candidates": []}
                else:
                    status = "timeout" if self._is_timeout_error(exc) else "failed"
                    result = {"candidates": []}
            discovered = [
                SearchCandidate.model_validate(item)
                for item in (result.get("candidates") or [])[:max_results]
            ]
            checkpoint.candidates.extend(discovered)
            record = SearchQueryRecord(
                query=query,
                status=status,
                candidate_count=len(discovered),
                requested_model=requested_search_model,
                actual_model=actual_model,
                fallback_reason=fallback_reason,
                original_error=original_error,
                elapsed_seconds=time.monotonic() - started,
            )
            checkpoint.query_records = [item for item in checkpoint.query_records if item.query != query] + [record]
            project.budget.attempted_model_calls += 1 + int(bool(fallback_reason))
            if status == "completed":
                project.budget.successful_model_calls += 1
                project.budget.used_model_calls += 1
            else:
                project.budget.failed_model_calls += 1
            checkpoint.elapsed_seconds += time.monotonic() - started
            produced += self._save_search_checkpoint(project, checkpoint)
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    phase,
                    "evidence_researcher",
                    status=f"search_query_{status}",
                    stage_substep="search_query",
                    requested_model=requested_search_model,
                    actual_model=actual_model,
                    fallback_used=actual_model != requested_search_model,
                    tool_names=["web_search"],
                    tool_name="web_search",
                    search_result_count=len(discovered),
                    successful_calls=int(status == "completed"),
                    failed_calls=int(status != "completed"),
                    attempted_calls=1,
                    model_call_count=1,
                ),
            )

        completed_queries = [item for item in checkpoint.query_records if item.status == "completed"]
        if not completed_queries:
            raise RuntimeError("All bounded evidence search queries failed or timed out.")
        self._append_event(
            project,
            completed_event(
                project.project_id,
                phase,
                "evidence_researcher",
                status="search_acquisition_completed",
                stage_substep="search_acquisition",
                query_count=len(checkpoint.search_plan.queries),
                search_result_count=len(checkpoint.candidates),
                visibility="user",
                display_key="bounded_search_completed",
                summary_markdown=(
                    f"完成{len(checkpoint.search_plan.queries)}条检索式中的{len(completed_queries)}条，"
                    f"共发现{len(checkpoint.candidates)}个候选来源。"
                ),
            ),
        )

        if not checkpoint.source_selection_completed:
            checkpoint.selected_candidates = select_sources(checkpoint.candidates, max_sources)
            checkpoint.source_selection_completed = True
            produced += self._save_search_checkpoint(project, checkpoint)
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    phase,
                    "evidence_researcher",
                    status="source_selection_completed",
                    stage_substep="source_selection",
                    search_result_count=len(checkpoint.selected_candidates),
                    visibility="user",
                    display_key="source_selection_completed",
                    summary_markdown=f"筛选出{len(checkpoint.selected_candidates)}个优先来源。",
                ),
            )

        extracted_texts: list[str] = []
        pending = [item for item in checkpoint.selected_candidates if item.extraction_status == "pending"]
        for offset in range(0, len(pending), 3):
            if elapsed_before_invocation + (time.monotonic() - invocation_started) >= total_budget:
                break
            batch = pending[offset : offset + 3]
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    phase,
                    "evidence_researcher",
                    status="source_extraction_started",
                    stage_substep="source_extraction",
                    tool_names=["web_search", "web_extractor"],
                    display_markdown=(
                        f"正在读取选定文献来源（{min(offset + len(batch), len(pending))}/{len(pending)}）……"
                    ),
                ),
            )
            started = time.monotonic()
            try:
                result = agent.extract_candidate_batch(
                    batch,
                    requested_search_model,
                    lambda event_type: self._source_extraction_progress(
                        project, phase, checkpoint, event_type
                    ),
                )
                text = str(result.get("final_text") or "")
                extracted_texts.append(text)
                status = "completed"
                error = ""
            except Exception as exc:
                status = "timeout" if self._is_timeout_error(exc) else "failed"
                error = self._sanitize_text(str(exc))
            for candidate in batch:
                updated = candidate.model_copy(
                    update={
                        "extraction_status": status,
                        "extracted_text": text if status == "completed" else "",
                        "extraction_error": error,
                    }
                )
                checkpoint.selected_candidates[checkpoint.selected_candidates.index(candidate)] = updated
            checkpoint.elapsed_seconds += time.monotonic() - started
            project.budget.attempted_model_calls += 1
            if status == "completed":
                project.budget.successful_model_calls += 1
                project.budget.used_model_calls += 1
            else:
                project.budget.failed_model_calls += 1
            produced += self._save_search_checkpoint(project, checkpoint)
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    phase,
                    "evidence_researcher",
                    status=f"source_extraction_{status}",
                    stage_substep="source_extraction",
                    successful_calls=int(status == "completed"),
                    failed_calls=int(status != "completed"),
                    extracted_page_count=len(batch) if status == "completed" else 0,
                ),
            )

        usable = [item for item in checkpoint.selected_candidates if item.extraction_status == "completed"]
        checkpoint.extraction_completed = not any(
            item.extraction_status == "pending" for item in checkpoint.selected_candidates
        )
        warnings = []
        if len(usable) < min_usable:
            warnings.append(
                f"Only {len(usable)} sources were extracted; the configured minimum is {min_usable}. Supplementary search is recommended."
            )
        if not usable:
            warnings.append("No source extraction completed; normalization will use selected source metadata only.")
        acquisition = SearchAcquisitionResult(
            final_text="\n\n".join(
                item.extracted_text for item in checkpoint.selected_candidates if item.extracted_text
            ),
            sources=[
                {
                    "title": item.title,
                    "url": item.url,
                    "site_name": item.source_domain,
                    "snippet": item.snippet,
                }
                for item in checkpoint.selected_candidates
            ],
            search_used=True,
            warnings=warnings,
            search_plan=checkpoint.search_plan,
            query_records=checkpoint.query_records,
            candidates=checkpoint.candidates,
            selected_candidates=checkpoint.selected_candidates,
            usable_source_count=len(usable),
            requested_model=requested_search_model,
            actual_model=requested_search_model,
        )
        search_record = self.artifacts.save_json(
            project.project_id, "search_acquisition", acquisition.model_dump(mode="json"), "evidence_researcher"
        )
        project.artifacts.append(search_record)
        checkpoint.search_artifact_id = search_record.artifact_id
        checkpoint.search_completed = True
        checkpoint.normalization_completed = False
        checkpoint.search_payload = acquisition.model_dump(mode="json")
        produced.append(search_record.artifact_id)
        self.store.save(project)
        self._append_event(
            project,
            completed_event(
                project.project_id,
                phase,
                "evidence_researcher",
                status="source_extraction_completed",
                stage_substep="source_extraction",
                extracted_page_count=len(usable),
                visibility="user",
                display_key="source_extraction_summary",
                summary_markdown=(
                    f"成功读取{len(usable)}个来源，"
                    f"{len(checkpoint.selected_candidates) - len(usable)}个来源读取失败或未完成。"
                ),
            ),
        )

        self._append_event(
            project,
            completed_event(
                project.project_id,
                phase,
                "evidence_researcher",
                status="evidence_normalization_started",
                stage_substep="evidence_normalization",
            ),
        )
        try:
            run = agent.normalize_search_result(project, acquisition)
        except Exception:
            project.budget.attempted_model_calls += 1
            project.budget.failed_model_calls += 1
            self.store.save(project)
            raise
        verified_collection = verify_evidence_collection(
            EvidenceCollection(
                evidence_items=run.output.evidence,
                preliminary_claims=run.output.claims,
                evidence_gaps=run.output.evidence_gaps,
                conflicting_evidence=run.output.conflicting_evidence,
                source_summary=run.output.confidence_summary,
            )
        )
        project.evidence, evidence_id_map = self._canonicalize_evidence_ids(
            enrich_evidence_items(verified_collection.evidence_items)
        )
        project.claims = self._remap_claim_evidence_refs(run.output.claims, evidence_id_map)
        project.evidence_gaps = run.output.evidence_gaps
        project.conflicting_evidence = run.output.conflicting_evidence
        run.output = run.output.model_copy(
            update={
                "evidence": project.evidence,
                "claims": project.claims,
            }
        )
        self._refresh_quality_metrics(project)
        checkpoint.normalization_completed = True
        produced += self._record_agent_output(project, phase, run, "evidence_map")
        project.active_artifact_versions["evidence"] = self._latest_artifact_version(project, "evidence_map")
        self._mark_downstream_artifacts_stale(project, from_artifact="evidence")
        self._append_event(
            project,
            completed_event(
                project.project_id,
                phase,
                "evidence_researcher",
                status="evidence_normalization_completed",
                stage_substep="evidence_normalization",
                search_result_count=len(acquisition.selected_candidates),
                display_markdown=render_evidence_summary(project.evidence, project.evidence_gaps),
                visibility="user",
                display_key="background_research_completed",
                summary_markdown=render_evidence_summary(project.evidence, project.evidence_gaps),
                evidence_count=len(project.evidence),
            ),
        )
        return produced

    def _run_background_research_legacy(self, project: ResearchProject, phase: ResearchPhase) -> list[str]:
        """Compatibility path for test doubles and projects created before bounded acquisition."""

        resolution = project.domain_resolution or resolve_domain(project.domain, project.secondary_domains)
        project.domain_resolution = resolution
        project.domain = resolution.canonical_primary_domain
        project.secondary_domains = resolution.canonical_secondary_domains
        agent = EvidenceResearcherAgent(client=self._structured_client_for_project(project))
        search_resolution = ModelRegistry(project.model_overrides).resolve_model("evidence_researcher")
        project.budget.attempted_model_calls += 1
        try:
            acquisition = agent.acquire_search(project, search_resolution.resolved_model)
        except Exception as exc:
            self._attach_search_model_diagnostics(exc, search_resolution)
            project.budget.failed_model_calls += 1
            self.store.save(project)
            raise
        project.budget.successful_model_calls += 1
        project.budget.used_model_calls += 1
        checkpoint = project.background_research_checkpoint
        record = self.artifacts.save_json(
            project.project_id, "search_acquisition", acquisition.model_dump(mode="json"), "evidence_researcher"
        )
        project.artifacts.append(record)
        checkpoint.search_artifact_id = record.artifact_id
        checkpoint.search_completed = True
        checkpoint.search_payload = acquisition.model_dump(mode="json")
        run = agent.normalize_search_result(project, acquisition)
        verified = verify_evidence_collection(
            EvidenceCollection(
                evidence_items=run.output.evidence,
                preliminary_claims=run.output.claims,
                evidence_gaps=run.output.evidence_gaps,
                conflicting_evidence=run.output.conflicting_evidence,
                source_summary=run.output.confidence_summary,
            )
        )
        project.evidence = enrich_evidence_items(verified.evidence_items)
        project.claims = run.output.claims
        project.evidence_gaps = run.output.evidence_gaps
        project.conflicting_evidence = run.output.conflicting_evidence
        checkpoint.normalization_completed = True
        produced = [record.artifact_id] + self._record_agent_output(project, phase, run, "evidence_map")
        self._append_event(
            project,
            completed_event(
                project.project_id,
                phase,
                "evidence_researcher",
                status="search_acquisition_completed",
                stage_substep="search_acquisition",
            ),
        )
        self._append_event(
            project,
            completed_event(
                project.project_id,
                phase,
                "evidence_researcher",
                status="evidence_normalization_completed",
                stage_substep="evidence_normalization",
                evidence_count=len(project.evidence),
            ),
        )
        return produced

    def _execute_claim_evidence_mapping(self, project: ResearchProject, phase: ResearchPhase) -> list[str]:
        produced: list[str] = []
        current_substep = "load_latest_evidence"
        current_artifact_type = "evidence_collection"
        try:
            self._claim_mapping_event(project, phase, "load_latest_evidence_started", current_substep)
            evidence_collection = self._latest_evidence_collection(project)
            self._claim_mapping_event(
                project,
                phase,
                "load_latest_evidence_completed",
                current_substep,
                evidence_count=len(evidence_collection.evidence_items),
            )

            current_substep = "evidence_reference_validation"
            self._claim_mapping_event(project, phase, "claim_mapping_validation_started", current_substep)
            self._validate_evidence_ids(evidence_collection)
            self._claim_mapping_event(project, phase, "claim_mapping_validation_completed", current_substep)

            current_substep = "model_output_parse"
            current_artifact_type = "claim_evidence_mapping"
            self._claim_mapping_event(project, phase, "claim_mapping_model_started", current_substep)
            self._ensure_budget(project, 1)
            mapping, metadata = self._run_claim_mapping(project, evidence_collection)
            self._claim_mapping_event(
                project,
                phase,
                "claim_mapping_model_completed",
                current_substep,
                requested_model=metadata.requested_model,
                actual_model=metadata.actual_model,
                fallback_used=metadata.fallback_used,
                attempted_calls=metadata.attempted_calls,
                successful_calls=metadata.successful_calls,
                model_call_count=metadata.successful_calls,
            )

            current_substep = "schema_validation"
            self._claim_mapping_event(project, phase, "claim_mapping_validation_started", current_substep)
            claims = self._canonicalize_claim_mapping(mapping, evidence_collection)
            graph = ClaimGraph(evidence_collection.evidence_items, claims)
            graph.validate_or_raise()
            errors = graph.validate()
            if errors:
                raise AIScientistError(
                    "Claim-evidence mapping schema validation failed.",
                    stage=phase.value,
                    substep=current_substep,
                    cause_type="ClaimGraphValidationError",
                    cause_message="; ".join(errors),
                    validation_errors=[{"msg": item} for item in errors],
                    artifact_type=current_artifact_type,
                    failure_category="orchestration_postprocess_error",
                    failing_component=current_substep,
                )
            self._claim_mapping_event(
                project,
                phase,
                "claim_mapping_validation_completed",
                current_substep,
                claim_count=len(claims),
                link_count=len(graph.to_dict()["links"]),
            )

            current_substep = "claim_graph_build"
            current_artifact_type = "claim_graph"
            self._claim_mapping_event(project, phase, "claim_graph_build_started", current_substep)
            graph_payload = graph.to_dict()
            self._claim_mapping_event(
                project,
                phase,
                "claim_graph_build_completed",
                current_substep,
                claim_count=len(claims),
                link_count=len(graph_payload["links"]),
            )

            current_substep = "artifact_save"
            current_artifact_type = "claim_evidence_mapping"
            self._claim_mapping_event(project, phase, "artifact_save_started", current_substep)
            mapping_record = self.artifacts.save_json(
                project.project_id,
                "claim_evidence_mapping",
                mapping.model_dump(mode="json"),
                "evidence_researcher",
            )
            graph_record = self.artifacts.save_json(
                project.project_id,
                "claim_graph",
                graph_payload,
                "evidence_researcher",
            )
            produced.extend([mapping_record.artifact_id, graph_record.artifact_id])
            self._claim_mapping_event(
                project,
                phase,
                "artifact_save_completed",
                current_substep,
                output_artifact_ids=[mapping_record.artifact_id, graph_record.artifact_id],
            )

            current_substep = "project_state_update"
            self._claim_mapping_event(project, phase, "project_state_update_started", current_substep)
            project.evidence = evidence_collection.evidence_items
            project.claims = claims
            project.artifacts.extend([mapping_record, graph_record])
            project.active_artifact_versions["claim_evidence_mapping"] = mapping_record.version
            project.active_artifact_versions["claim_graph"] = graph_record.version
            for artifact_type in ["claim_evidence_mapping", "claim_graph"]:
                if artifact_type in project.stale_artifacts:
                    project.stale_artifacts.remove(artifact_type)
            self._refresh_quality_metrics(project)
            if mapping.display_markdown:
                project.stage_messages.append(mapping.display_markdown)
            else:
                project.stage_messages.append(render_claim_mapping(project))
            project.budget.attempted_model_calls += metadata.attempted_calls
            project.budget.successful_model_calls += metadata.successful_calls
            project.budget.failed_model_calls += metadata.failed_calls
            project.budget.fallback_model_calls += metadata.fallback_calls
            project.budget.used_model_calls += metadata.successful_calls
            self.store.save(project)
            self._claim_mapping_event(
                project,
                phase,
                "project_state_update_completed",
                current_substep,
                output_artifact_ids=[mapping_record.artifact_id, graph_record.artifact_id],
                requested_model=metadata.requested_model,
                actual_model=metadata.actual_model,
                fallback_used=metadata.fallback_used,
                attempted_calls=metadata.attempted_calls,
                successful_calls=metadata.successful_calls,
                model_call_count=metadata.successful_calls,
                display_markdown=render_claim_mapping(project),
                visibility="user",
                display_key="claim_evidence_mapping_completed",
                summary_markdown=render_claim_mapping(project),
            )

            current_substep = "phase_transition"
            self._claim_mapping_event(project, phase, "phase_transition_started", current_substep)
            self.state_machine.transition(project, "next")
            self.store.save(project)
            self._claim_mapping_event(project, phase, "phase_transition_completed", current_substep)
            return produced
        except InvalidEvidenceReferenceError:
            raise
        except AIScientistError:
            raise
        except Exception as exc:
            raise AIScientistError(
                "Claim-evidence mapping post-processing failed.",
                stage=phase.value,
                substep=current_substep,
                cause_type=type(exc).__name__,
                cause_message=str(exc),
                validation_errors=self._validation_errors(exc),
                artifact_type=current_artifact_type,
                failure_category="orchestration_postprocess_error",
                failing_component=current_substep,
            ) from exc

    def _claim_mapping_event(
        self,
        project: ResearchProject,
        phase: ResearchPhase,
        status: str,
        stage_substep: str,
        **kwargs: Any,
    ) -> None:
        self._append_event(
            project,
            completed_event(
                project.project_id,
                phase,
                "evidence_researcher",
                status=status,
                stage_substep=stage_substep,
                failing_component=None,
                **kwargs,
            ),
        )

    def _latest_evidence_collection(self, project: ResearchProject) -> EvidenceCollection:
        return EvidenceCollection(
            evidence_items=project.evidence,
            preliminary_claims=project.claims,
            evidence_gaps=project.evidence_gaps,
            conflicting_evidence=project.conflicting_evidence,
            source_summary=f"{len(project.evidence)} active evidence records are available.",
        )

    @staticmethod
    def _validate_evidence_ids(collection: EvidenceCollection) -> None:
        seen: set[str] = set()
        for index, item in enumerate(collection.evidence_items, start=1):
            if not item.evidence_id:
                raise AIScientistError(
                    f"Evidence item {index} has an empty evidence_id.",
                    stage=ResearchPhase.CLAIM_EVIDENCE_MAPPING.value,
                    substep="evidence_reference_validation",
                    cause_type="MissingEvidenceId",
                    cause_message=f"Evidence item {index} has an empty evidence_id.",
                    artifact_type="evidence_collection",
                    failure_category="orchestration_postprocess_error",
                    failing_component="evidence_reference_validation",
                )
            if item.evidence_id in seen:
                raise AIScientistError(
                    f"Duplicate evidence_id in active evidence collection: {item.evidence_id}",
                    stage=ResearchPhase.CLAIM_EVIDENCE_MAPPING.value,
                    substep="evidence_reference_validation",
                    cause_type="DuplicateEvidenceId",
                    cause_message=item.evidence_id,
                    artifact_type="evidence_collection",
                    failure_category="orchestration_postprocess_error",
                    failing_component="evidence_reference_validation",
                )
            seen.add(item.evidence_id)

    @staticmethod
    def _canonicalize_evidence_ids(evidence: list[EvidenceItem]) -> tuple[list[EvidenceItem], dict[str, str]]:
        canonical: list[EvidenceItem] = []
        id_map: dict[str, str] = {}
        for index, item in enumerate(evidence, start=1):
            canonical_id = f"EVD-{index:03d}"
            id_map[item.evidence_id] = canonical_id
            if item.title:
                id_map[item.title] = canonical_id
            if item.source_url:
                id_map[item.source_url] = canonical_id
            canonical.append(item.model_copy(update={"evidence_id": canonical_id}))
        return canonical, id_map

    @staticmethod
    def _remap_claim_evidence_refs(claims: list[Claim], evidence_id_map: dict[str, str]) -> list[Claim]:
        remapped: list[Claim] = []
        for claim in claims:
            remapped.append(
                claim.model_copy(
                    update={
                        "supporting_evidence_ids": [
                            evidence_id_map.get(item, item) for item in claim.supporting_evidence_ids
                        ],
                        "contradicting_evidence_ids": [
                            evidence_id_map.get(item, item) for item in claim.contradicting_evidence_ids
                        ],
                    }
                )
            )
        return remapped

    @staticmethod
    def _canonicalize_claim_mapping(
        mapping: ClaimEvidenceMappingResult,
        collection: EvidenceCollection,
    ) -> list[Claim]:
        evidence_ids = {item.evidence_id for item in collection.evidence_items}
        temporary_claim_ids: dict[str, str] = {}
        claims: list[Claim] = []
        for index, item in enumerate(mapping.claims, start=1):
            canonical_id = f"CLM-{index:03d}"
            if item.claim_id:
                temporary_claim_ids[item.claim_id] = canonical_id
            temporary_claim_ids[item.statement] = canonical_id
            claim_type = item.claim_type if item.claim_type in {"observation", "reported_fact", "inference", "hypothesis", "prediction", "conclusion"} else "reported_fact"
            status = item.status if item.status in {"supported", "partially_supported", "disputed", "unsupported", "unknown"} else "unknown"
            claims.append(
                Claim(
                    claim_id=canonical_id,
                    statement=item.statement,
                    claim_type=claim_type,  # type: ignore[arg-type]
                    status=status,  # type: ignore[arg-type]
                    assumptions=item.assumptions,
                    limitations=item.limitations,
                    supporting_evidence_ids=[],
                    contradicting_evidence_ids=[],
                )
            )
        claim_by_id = {claim.claim_id: claim for claim in claims}
        for link in mapping.links:
            claim_id = temporary_claim_ids.get(link.claim_id, link.claim_id)
            evidence_id = link.evidence_id
            if claim_id not in claim_by_id:
                raise AIScientistError(
                    f"Claim-evidence link references unknown claim {link.claim_id}.",
                    stage=ResearchPhase.CLAIM_EVIDENCE_MAPPING.value,
                    substep="evidence_reference_validation",
                    cause_type="InvalidClaimReferenceError",
                    cause_message=f"Unknown claim_id: {link.claim_id}",
                    artifact_type="claim_evidence_mapping",
                    failure_category="orchestration_postprocess_error",
                    failing_component="evidence_reference_validation",
                )
            if evidence_id not in evidence_ids:
                raise InvalidEvidenceReferenceError(claim_id, evidence_id)
            claim = claim_by_id[claim_id]
            if link.relation == "supports" and evidence_id not in claim.supporting_evidence_ids:
                claim.supporting_evidence_ids.append(evidence_id)
            elif link.relation == "contradicts" and evidence_id not in claim.contradicting_evidence_ids:
                claim.contradicting_evidence_ids.append(evidence_id)
        for item, claim in zip(mapping.claims, claims, strict=False):
            for evidence_id in item.supporting_evidence_ids:
                if evidence_id not in evidence_ids:
                    raise InvalidEvidenceReferenceError(claim.claim_id, evidence_id)
                if evidence_id not in claim.supporting_evidence_ids:
                    claim.supporting_evidence_ids.append(evidence_id)
            for evidence_id in item.contradicting_evidence_ids:
                if evidence_id not in evidence_ids:
                    raise InvalidEvidenceReferenceError(claim.claim_id, evidence_id)
                if evidence_id not in claim.contradicting_evidence_ids:
                    claim.contradicting_evidence_ids.append(evidence_id)
        return claims

    def _run_agent(self, project: ResearchProject, agent_class: type) -> AgentRun:
        self._ensure_budget(project, 1)
        try:
            return agent_class(client=self._structured_client_for_project(project)).run(project)
        except Exception:
            project.budget.attempted_model_calls += 1
            project.budget.failed_model_calls += 1
            self.store.save(project)
            raise

    def _structured_client_for_project(self, project: ResearchProject) -> StructuredQwenClient:
        return StructuredQwenClient(registry=ModelRegistry(project.model_overrides))

    def _run_domain_selection(self, project: ResearchProject) -> tuple[DomainSelectionOutput, StructuredCallMetadata]:
        payload = {
            "question": project.question.model_dump(mode="json") if project.question else None,
            "objective": project.objective,
            "domain_hint": project.domain_hint,
            "research_mode": project.research_mode.value if project.research_mode else None,
            "constraints": project.constraints,
            "instruction": (
                "Select the primary research domain and at most two secondary domains. "
                "For remote-work productivity or software-engineering human productivity questions, "
                "prefer social_science with computer_science as a possible secondary domain."
            ),
        }
        try:
            result = self._structured_client_for_project(project).call(
                "research_director",
                "You are an AI Scientist domain analyst. Return structured domain routing only.",
                payload,
                DomainSelectionOutput,
            )
            return result.value, result.metadata
        except Exception:
            project.budget.attempted_model_calls += 1
            project.budget.failed_model_calls += 1
            self.store.save(project)
            raise

    def _run_claim_mapping(
        self,
        project: ResearchProject,
        evidence_collection: EvidenceCollection,
        track_failure_budget: bool = True,
    ) -> tuple[ClaimEvidenceMappingResult, StructuredCallMetadata]:
        payload = {
            "question": project.question.model_dump(mode="json") if project.question else None,
            "project_id": project.project_id,
            "evidence_collection": evidence_collection.model_dump(mode="json"),
            "task": (
                "Build a claim-evidence mapping using only the current EvidenceCollection. "
                "Use only provided evidence_ids in links and evidence ID lists. "
                "Do not invent new evidence, URLs, DOI, authors, or citations. "
                "Mark unsupported or disputed claims explicitly."
            ),
        }
        try:
            result = self._structured_client_for_project(project).call(
                "evidence_researcher",
                "You are an AI Scientist evidence mapper. Return claim-evidence mapping JSON only.",
                payload,
                ClaimEvidenceMappingResult,
            )
            return result.value, result.metadata
        except Exception:
            if track_failure_budget:
                project.budget.attempted_model_calls += 1
                project.budget.failed_model_calls += 1
                self.store.save(project)
            raise

    def _record_agent_output(
        self,
        project: ResearchProject,
        phase: ResearchPhase,
        run: AgentRun,
        artifact_type: str,
    ) -> list[str]:
        extra_calls = int(run.auxiliary.get("search_model_calls", 0))
        attempted_calls = run.metadata.attempted_calls + extra_calls
        successful_calls = run.metadata.successful_calls + extra_calls
        fallback_calls = run.metadata.fallback_calls
        if project.budget.used_model_calls + successful_calls > project.budget.max_model_calls:
            raise BudgetExceededError("AI Scientist model-call budget was exceeded during this stage.")
        project.budget.attempted_model_calls += attempted_calls
        project.budget.successful_model_calls += successful_calls
        project.budget.fallback_model_calls += fallback_calls
        project.budget.used_model_calls += successful_calls
        record = self.artifacts.save_json(
            project.project_id,
            artifact_type,
            run.output.model_dump(mode="json"),
            run.metadata.agent_name,
        )
        project.artifacts.append(record)
        display_markdown = self._stage_display_markdown(project, artifact_type, run.output)
        if display_markdown:
            project.stage_messages.append(display_markdown)
        event = completed_event(
            project.project_id,
            phase,
            run.metadata.agent_name,
            job_id=_CURRENT_JOB_ID.get(),
            requested_model=run.metadata.requested_model,
            actual_model=run.metadata.actual_model,
            fallback_used=run.metadata.fallback_used,
            output_artifact_ids=[record.artifact_id],
            tool_names=run.tool_names,
            token_usage=run.metadata.token_usage,
            started_at=run.metadata.started_at,
            display_markdown=display_markdown,
            attempted_calls=attempted_calls,
            successful_calls=successful_calls,
            failed_calls=0,
            fallback_calls=fallback_calls,
            query_count=_optional_int(run.auxiliary.get("query_count")),
            search_result_count=_optional_int(run.auxiliary.get("search_result_count")),
            extracted_page_count=_optional_int(run.auxiliary.get("extracted_page_count")),
            model_call_count=successful_calls,
        )
        self._append_event(project, event)
        self.store.save(project)
        return [record.artifact_id]

    def _stage_display_markdown(self, project: ResearchProject, artifact_type: str, output: Any) -> str:
        if artifact_type == "research_question":
            return render_research_question(project.question)
        if artifact_type == "research_mode_selection":
            return render_method_selection(output if isinstance(output, MethodologyOutput) else None, project)
        if artifact_type == "evidence_map":
            return render_evidence_summary(project.evidence, project.evidence_gaps)
        if artifact_type == "claim_evidence_validation":
            return render_claim_mapping(project)
        if artifact_type == "hypotheses":
            return render_hypotheses(project.hypotheses)
        if artifact_type == "methodology":
            return render_method_selection(output if isinstance(output, MethodologyOutput) else None, project)
        if artifact_type == "study_design":
            return render_study_design(project.study_design)
        if artifact_type == "analysis_plan":
            return render_analysis_plan(project.analysis_plan)
        if artifact_type == "independent_review":
            return render_feasibility_review(project.reviews[-1] if project.reviews else None)
        if artifact_type == "research_plan_synthesis":
            return render_synthesis(project.conclusion)
        return ""

    def _record_structured_output(
        self,
        project: ResearchProject,
        phase: ResearchPhase,
        created_by: str,
        output: Any,
        artifact_type: str,
        metadata: StructuredCallMetadata | None = None,
        display_markdown: str = "",
    ) -> list[str]:
        content = output.model_dump(mode="json") if hasattr(output, "model_dump") else output
        record = self.artifacts.save_json(project.project_id, artifact_type, content, created_by)
        project.artifacts.append(record)
        if metadata:
            project.budget.attempted_model_calls += metadata.attempted_calls
            project.budget.successful_model_calls += metadata.successful_calls
            project.budget.failed_model_calls += metadata.failed_calls
            project.budget.fallback_model_calls += metadata.fallback_calls
            project.budget.used_model_calls += metadata.successful_calls
        if display_markdown:
            project.stage_messages.append(display_markdown)
        event = completed_event(
            project.project_id,
            phase,
            created_by,
            job_id=_CURRENT_JOB_ID.get(),
            requested_model=metadata.requested_model if metadata else None,
            actual_model=metadata.actual_model if metadata else None,
            fallback_used=metadata.fallback_used if metadata else False,
            output_artifact_ids=[record.artifact_id],
            display_markdown=display_markdown,
            attempted_calls=metadata.attempted_calls if metadata else 0,
            successful_calls=metadata.successful_calls if metadata else 0,
            failed_calls=metadata.failed_calls if metadata else 0,
            fallback_calls=metadata.fallback_calls if metadata else 0,
            model_call_count=metadata.successful_calls if metadata else None,
        )
        self._append_event(project, event)
        return [record.artifact_id]

    def _record_human_edit(
        self,
        project: ResearchProject,
        artifact_type: str,
        content: Any,
        reason: str,
    ) -> list[str]:
        record = self.artifacts.save_json(project.project_id, artifact_type, content, "human")
        project.artifacts.append(record)
        event = completed_event(
            project.project_id,
            project.phase,
            "human",
            status="human_edit",
            output_artifact_ids=[record.artifact_id],
            changed_fields=list(content) if isinstance(content, dict) else [artifact_type],
            reason=reason,
            feedback=reason,
        )
        self._append_event(project, event)
        return [record.artifact_id]

    def _targeted_rollback(
        self,
        project: ResearchProject,
        target: str,
        reason: str,
        changed_fields: list[str],
    ) -> None:
        target_map = {
            "question": ResearchPhase.QUESTION_FORMULATION,
            "evidence": ResearchPhase.BACKGROUND_RESEARCH,
            "hypothesis": ResearchPhase.HYPOTHESIS_GENERATION,
            "method": ResearchPhase.METHOD_SELECTION,
            "design": ResearchPhase.STUDY_DESIGN,
            "analysis": ResearchPhase.ANALYSIS_PLANNING,
        }
        target_phase = target_map[target]
        previous_phase = project.phase
        invalidated = self._artifact_ids_from_phase(target_phase, project)
        preserved = [item.artifact_id for item in project.artifacts if item.artifact_id not in set(invalidated)]
        project.phase = target_phase
        event = completed_event(
            project.project_id,
            previous_phase,
            "human",
            status="targeted_rollback",
            previous_phase=previous_phase,
            target_phase=target_phase,
            revision_reason=reason,
            changed_fields=changed_fields,
            invalidated_artifact_ids=invalidated,
            preserved_artifact_ids=preserved,
        )
        self._append_event(project, event)

    def _refresh_quality_metrics(self, project: ResearchProject) -> None:
        project.quality_metrics = compute_quality_metrics(project)

    def _write_research_plan(self, project: ResearchProject) -> list[str]:
        md = build_research_plan_markdown(project)
        js = build_research_plan_json(project)
        md_record = self.artifacts.save_named_text(
            project.project_id,
            "research_plan.md",
            "research_plan",
            md,
            "scientific_synthesizer",
        )
        json_record = self.artifacts.save_named_json(
            project.project_id,
            "research_plan.json",
            "research_plan",
            js,
            "scientific_synthesizer",
        )
        project.artifacts.extend([md_record, json_record])
        event = completed_event(
            project.project_id,
            ResearchPhase.SYNTHESIS,
            "report_writer",
            output_artifact_ids=[md_record.artifact_id, json_record.artifact_id],
        )
        self._append_event(project, event)
        return [md_record.artifact_id, json_record.artifact_id]

    @staticmethod
    def _artifact_ids_from_phase(target_phase: ResearchPhase, project: ResearchProject) -> list[str]:
        phase_artifacts = {
            ResearchPhase.QUESTION_FORMULATION: {"research_question", "research_mode_selection", "domain_selection"},
            ResearchPhase.BACKGROUND_RESEARCH: {"evidence_map", "claim_evidence_mapping", "claim_graph"},
            ResearchPhase.HYPOTHESIS_GENERATION: {"hypotheses"},
            ResearchPhase.METHOD_SELECTION: {"methodology"},
            ResearchPhase.STUDY_DESIGN: {"study_design"},
            ResearchPhase.ANALYSIS_PLANNING: {"analysis_plan", "reproducibility_plan", "independent_review", "research_plan"},
        }
        affected = phase_artifacts.get(target_phase, set())
        return [item.artifact_id for item in project.artifacts if item.artifact_type in affected]

    @staticmethod
    def _latest_artifact_version(project: ResearchProject, artifact_type: str) -> int | None:
        versions = [item.version for item in project.artifacts if item.artifact_type == artifact_type]
        return max(versions, default=None)

    @staticmethod
    def _mark_downstream_artifacts_stale(project: ResearchProject, from_artifact: str) -> None:
        downstream = {
            "evidence": [
                "claim_evidence_mapping",
                "claim_graph",
                "hypotheses",
                "methodology",
                "study_design",
                "analysis_plan",
                "independent_review",
                "critical_review",
                "research_plan",
            ]
        }
        for artifact_type in downstream.get(from_artifact, []):
            project.active_artifact_versions[artifact_type] = None
            if artifact_type not in project.stale_artifacts:
                project.stale_artifacts.append(artifact_type)

    def _transition_event(self, project: ResearchProject, outcome: str) -> None:
        previous = project.phase
        self.state_machine.transition(project, outcome)
        event = completed_event(
            project.project_id,
            previous,
            "orchestrator",
            job_id=_CURRENT_JOB_ID.get(),
            status=f"transition:{outcome}:{project.phase.value}",
        )
        self._append_event(project, event)

    @staticmethod
    def _agent_name_for_phase(phase: ResearchPhase) -> str:
        return {
            ResearchPhase.QUESTION_FORMULATION: "research_director",
            ResearchPhase.BACKGROUND_RESEARCH: "evidence_researcher",
            ResearchPhase.HYPOTHESIS_GENERATION: "hypothesis_scientist",
            ResearchPhase.METHOD_SELECTION: "methodologist",
            ResearchPhase.STUDY_DESIGN: "study_designer",
            ResearchPhase.ANALYSIS_PLANNING: "analyst",
            ResearchPhase.FEASIBILITY_REVIEW: "skeptical_reviewer",
            ResearchPhase.CRITICAL_REVIEW: "skeptical_reviewer",
            ResearchPhase.SYNTHESIS: "scientific_synthesizer",
        }.get(phase, "orchestrator")

    def _append_event(self, project: ResearchProject, event: ResearchEvent) -> None:
        if event.visibility == "user" and event.display_key:
            dedupe_key = f"{project.project_id}:{event.phase.value}:{project.iteration}:{event.display_key}"
            if dedupe_key in project.user_event_keys:
                return
            project.user_event_keys.append(dedupe_key)
        project.events.append(event.event_id)
        self.store.append_event(event)
        self.store.save(project)

    def _save_search_checkpoint(self, project: ResearchProject, checkpoint: Any) -> list[str]:
        record = self.artifacts.save_json(
            project.project_id,
            "search_checkpoint",
            checkpoint.model_dump(mode="json"),
            "evidence_researcher",
        )
        project.artifacts.append(record)
        self.store.save(project)
        return [record.artifact_id]

    def _source_extraction_progress(
        self,
        project: ResearchProject,
        phase: ResearchPhase,
        checkpoint: Any,
        event_type: str,
    ) -> None:
        checkpoint.last_activity_at = utc_now()
        self._append_event(
            project,
            completed_event(
                project.project_id,
                phase,
                "evidence_researcher",
                status="source_extraction_progress",
                stage_substep="source_extraction",
                reason=event_type,
            ),
        )

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        names = {type(exc).__name__.lower()}
        cause = exc.__cause__ or exc.__context__
        if cause:
            names.add(type(cause).__name__.lower())
        message = str(exc).lower()
        return any("timeout" in name for name in names) or "timed out" in message or "timeout" in message

    @staticmethod
    def _apply_call_metadata(project: ResearchProject, metadata: StructuredCallMetadata) -> None:
        project.budget.attempted_model_calls += metadata.attempted_calls
        project.budget.successful_model_calls += metadata.successful_calls
        project.budget.failed_model_calls += metadata.failed_calls
        project.budget.fallback_model_calls += metadata.fallback_calls
        project.budget.used_model_calls += metadata.successful_calls

    @staticmethod
    def _ensure_budget(project: ResearchProject, required_calls: int) -> None:
        if project.budget.used_model_calls + required_calls > project.budget.max_model_calls:
            raise BudgetExceededError("AI Scientist model-call budget is exhausted.")

    @staticmethod
    def _responses_endpoint_host() -> str:
        base_url = os.getenv("RESPONSES_BASE_URL") or os.getenv(
            "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        return urlparse(base_url).netloc

    def _attach_search_model_diagnostics(self, exc: Exception, resolution: Any) -> None:
        if not getattr(exc, "requested_model", None):
            setattr(exc, "requested_model", resolution.resolved_model)
        if not getattr(exc, "actual_model", None):
            setattr(exc, "actual_model", resolution.resolved_model)
        if not getattr(exc, "endpoint_host", None):
            setattr(exc, "endpoint_host", self._responses_endpoint_host())
        if not getattr(exc, "tool_names", None):
            setattr(exc, "tool_names", ["web_search", "web_extractor"])
        if getattr(exc, "previous_response_id_present", None) is None:
            setattr(exc, "previous_response_id_present", False)

    @staticmethod
    def _sanitize_error(exc: Exception) -> str:
        message = f"{type(exc).__name__}: {exc}"
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        return message.replace(api_key, "[REDACTED_API_KEY]") if api_key else message

    @staticmethod
    def _sanitize_text(text: str) -> str:
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        return text.replace(api_key, "[REDACTED_API_KEY]") if api_key else text

    @classmethod
    def _sanitize_traceback(cls, exc: Exception) -> str:
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        return text.replace(api_key, "[REDACTED_API_KEY]") if api_key else text

    @staticmethod
    def _substep_for_error(phase: ResearchPhase, exc: Exception) -> str:
        if getattr(exc, "substep", None):
            return str(getattr(exc, "substep"))
        if phase == ResearchPhase.BACKGROUND_RESEARCH:
            message = str(exc).lower()
            if "search" in message or "responses api" in message:
                return "search_acquisition"
            if "validation" in message or "schema" in message:
                return "evidence_schema_validation"
            if "source" in message or "evidence" in message:
                return "evidence_normalization"
        if phase == ResearchPhase.CLAIM_EVIDENCE_MAPPING:
            message = str(exc).lower()
            if "evidence_id" in message or "evidence reference" in message:
                return "evidence_reference_validation"
            if "schema" in message or "validation" in message:
                return "schema_validation"
            if "artifact" in message or "save" in message:
                return "artifact_save"
            if "transition" in message:
                return "phase_transition"
            return "orchestration_postprocess"
        return "model_call"

    @staticmethod
    def _failing_component_for_substep(stage_substep: str) -> str:
        return {
            "domain_skill_resolution": "domain_skill_resolution",
            "search_acquisition": "search_acquisition",
            "evidence_normalization": "evidence_normalization",
            "evidence_schema_validation": "evidence_schema_validation",
            "load_latest_evidence": "load_latest_evidence",
            "model_output_parse": "model_output_parse",
            "schema_validation": "schema_validation",
            "evidence_reference_validation": "evidence_reference_validation",
            "claim_graph_build": "claim_graph_build",
            "artifact_save": "artifact_save",
            "project_state_update": "project_state_update",
            "phase_transition": "phase_transition",
            "orchestration_postprocess": "orchestration_postprocess",
        }.get(stage_substep, "model_call")

    @staticmethod
    def _failure_display_message(phase: ResearchPhase, stage_substep: str) -> str:
        if phase == ResearchPhase.BACKGROUND_RESEARCH:
            return {
                "domain_skill_resolution": "系统无法匹配专用学科规则，已尝试使用通用研究规则。",
                "search_acquisition": "联网证据检索未成功，项目已保留在上一个完整阶段。",
                "evidence_normalization": "证据已经检索完成，但整理为研究证据时出现问题，可以直接重试整理，无需重新搜索。",
                "evidence_schema_validation": "部分证据信息不完整，系统未将不可靠内容写入正式研究记录。",
            }.get(stage_substep, "证据研究阶段执行失败，项目已保留在上一个完整阶段，可以重试。")
        if phase == ResearchPhase.CLAIM_EVIDENCE_MAPPING:
            return {
                "model_output_parse": "主张整理结果未能通过格式检查，项目已保留现有证据。",
                "schema_validation": "主张整理结果未能通过结构校验，项目已保留现有证据。",
                "evidence_reference_validation": "部分主张引用了不存在或已失效的证据，系统未写入不一致结果。",
                "claim_graph_build": "主张与证据关系图构建失败，系统未写入半成品结果。",
                "artifact_save": "主张与证据已经整理完成，但保存研究产物时失败。",
                "project_state_update": "主张与证据已经整理完成，但项目状态更新失败，可以安全重试。",
                "phase_transition": "研究内容已经生成，但流程状态未能推进，可以安全重试。",
            }.get(stage_substep, "主张与证据映射阶段执行失败，项目已保留在当前阶段，可以重试。")
        return f"{phase.value} 阶段执行失败，项目已保留在上一个完整阶段。"

    @staticmethod
    def _validation_errors(exc: Exception) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        current: BaseException | None = exc
        while current:
            if hasattr(current, "errors") and callable(getattr(current, "errors")):
                try:
                    raw_errors = current.errors()
                except Exception:
                    raw_errors = []
                for item in raw_errors:
                    if isinstance(item, dict):
                        errors.append({"loc": item.get("loc"), "type": item.get("type"), "msg": item.get("msg")})
                if errors:
                    return errors
            current = current.__cause__
        return []


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None
