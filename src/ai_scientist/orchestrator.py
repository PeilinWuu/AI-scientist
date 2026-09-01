"""Sequential orchestration for structured, auditable research planning."""

from __future__ import annotations

import json
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
    RevisionVerifierAgent,
    ScientificSynthesizerAgent,
    SkepticalReviewerAgent,
    StudyDesignerAgent,
)
from src.ai_scientist.agents.base_agent import AgentRun
from src.ai_scientist.artifact_store import ArtifactStore
from src.ai_scientist.claim_graph import ClaimGraph
from src.ai_scientist.competition_runtime import CompetitionRuntime
from src.ai_scientist.domain_resolution import resolve_domain
from src.ai_scientist.domain_router import DomainRouter
from src.ai_scientist.document_parsers import parse_research_asset as parse_local_research_asset
from src.ai_scientist.events import completed_event
from src.ai_scientist.evidence_verifier import verify_evidence_collection
from src.ai_scientist.evidence_curation import (
    bind_search_plan,
    compute_question_hash,
    deterministic_plan_relevance,
    enrich_candidates,
    parse_human_source,
    research_question_version,
    summarize_source_feedback,
    validate_checkpoint_binding,
    validate_search_plan_binding,
)
from src.ai_scientist.exceptions import (
    AIScientistError,
    BudgetExceededError,
    InvalidEvidenceReferenceError,
    InvalidTransitionError,
    InsufficientEvidenceForClaimMapping,
    ResearchAssetNotFoundError,
    StaleSearchPlanError,
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
from src.ai_scientist.revision_workflow import (
    build_approved_revision_plan,
    combine_revision_verification_results,
    deterministic_verify_batch,
    normalize_completion_criteria,
    normalize_revision_plan,
)
from src.ai_scientist.schemas import (
    AnalysisPlan,
    ApprovedRevisionPlan,
    BackgroundResearchCheckpoint,
    Claim,
    ClaimEvidenceMappingResult,
    DomainSelectionOutput,
    EvidenceCollection,
    EvidenceReviewMode,
    EvidenceResearchOutput,
    EvidenceItem,
    SearchAcquisitionResult,
    SearchCandidate,
    SearchPlan,
    SearchQueryRecord,
    CuratedSource,
    ResearchAsset,
    SourceCandidateCollection,
    SourceDecisionInput,
    SourceSelectionSnapshot,
    SelectionProvenance,
    Hypothesis,
    HumanApprovalRecord,
    HumanRevisionRecord,
    MethodologyOutput,
    ResearchEvent,
    ResearchMode,
    ResearchPhase,
    ResearchQuestion,
    ResearchProject,
    ReviewResult,
    ReviewPackage,
    RevisionAction,
    RevisionIssue,
    RevisionIssueDecision,
    RevisionCriterionResult,
    RevisionTargetBatch,
    RevisionVerificationResult,
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


RESEARCH_ASSET_SUFFIXES = {
    ".pdf",
    ".md",
    ".txt",
    ".csv",
    ".tsv",
    ".json",
    ".xml",
    ".xlsx",
    ".xls",
}
RESEARCH_ASSET_PURPOSES = {"reference", "data", "other"}
RESEARCH_ASSET_UPLOAD_CONTEXTS = {
    "project_creation",
    "search_plan_review",
    "source_review",
    "revision_review",
    "human_approval",
    "project_workspace",
    "experimental_result",
}


_CURRENT_JOB_ID: ContextVar[str | None] = ContextVar("current_research_job_id", default=None)

INTERNAL_EXECUTOR_BINDINGS = {
    "competition_1b_damped_oscillator": "damped_oscillator_v1",
}


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
        self.revision_max_attempts = min(
            3,
            max(1, int(os.getenv("AI_SCIENTIST_REVISION_MAX_ATTEMPTS", "3"))),
        )

    def create_project(
        self,
        objective: str,
        domain_hint: str | None = None,
        constraints_text: str = "",
        constraints: dict[str, Any] | None = None,
        model_overrides: dict[str, str | None] | None = None,
        max_iterations: int = 2,
        planning_only: bool = True,
        evidence_review_mode: EvidenceReviewMode = "ASSISTED",
        reproducibility_seed: int | None = None,
    ) -> ResearchProject:
        objective = objective.strip()
        if not objective:
            raise ValueError("Research question is required.")
        max_calls = int(os.getenv("AI_SCIENTIST_MAX_MODEL_CALLS", "50"))
        resolved_constraints = {
            **(constraints or {}),
            **({"constraints_text": constraints_text} if constraints_text else {}),
        }
        executor_binding = INTERNAL_EXECUTOR_BINDINGS.get(str(resolved_constraints.get("example_case") or ""))
        if executor_binding:
            execution_capability = "INTERNAL_EXECUTABLE"
            planning_only = False
        elif planning_only:
            execution_capability = "PLANNING_ONLY"
        else:
            execution_capability = "EXTERNAL_EXECUTION_REQUIRED"
        available_tools = self.tools.available_names()
        if executor_binding:
            available_tools = [*available_tools, executor_binding]
        project = ResearchProject(
            title=objective[:100],
            objective=objective,
            domain_hint=domain_hint,
            constraints=resolved_constraints,
            model_overrides=normalize_model_overrides(model_overrides),
            max_iterations=max_iterations,
            planning_only=planning_only,
            execution_capability=execution_capability,
            executor_binding=executor_binding,
            evidence_review_mode=evidence_review_mode,
            reproducibility_seed=reproducibility_seed,
            budget={
                "max_model_calls": max_calls,
                "max_iterations": max_iterations,
            },
            available_tools=available_tools,
            missing_capabilities=self.tools.unavailable_names(),
        )
        self.store.save(project)
        event = completed_event(
            project.project_id,
            ResearchPhase.INTAKE,
            "orchestrator",
            status="created",
            visibility="user",
            display_key="project_created",
            display_markdown="研究项目已创建，可以开始逐阶段形成科研方案。",
        )
        self._append_event(project, event)
        if executor_binding == "damped_oscillator_v1":
            observations = (
                Path(__file__).resolve().parents[2]
                / "competition"
                / "1b"
                / "cases"
                / "flagship"
                / "input"
                / "observations.csv"
            )
            if observations.is_file():
                project = self.register_research_asset(
                    project.project_id,
                    observations.name,
                    "text/csv",
                    observations.read_bytes(),
                    purpose="data",
                    description="Competition 1B damped-oscillator example observations (seed 20260831).",
                    upload_context="project_creation",
                    source="bundled_example",
                )
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
        project.active_job_id = job_id
        self.store.save(project)
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
                self._refresh_internal_data_executor_binding(project)
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
                if project.phase == ResearchPhase.BACKGROUND_RESEARCH:
                    self.state_machine.transition(project, "next")
                elif project.phase == ResearchPhase.SEARCH_PLAN_REVIEW:
                    stage_status = "awaiting_search_plan_review"
                elif project.phase == ResearchPhase.HUMAN_SOURCE_REVIEW:
                    stage_status = "awaiting_human_source_review"
            elif project.phase == ResearchPhase.SEARCH_PLAN_REVIEW:
                stage_status = "awaiting_search_plan_review"
            elif project.phase == ResearchPhase.HUMAN_SOURCE_REVIEW:
                stage_status = "awaiting_human_source_review"
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
                self._refresh_internal_data_executor_binding(project)
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
                prepared_review = self._prepare_review_for_project(project, review.output)
                gated_review = apply_reviewer_quality_gates(prepared_review, project.quality_metrics)
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
            elif project.phase == ResearchPhase.HUMAN_REVISION_REVIEW:
                stage_status = "awaiting_human_revision_review"
            elif project.phase == ResearchPhase.HUMAN_INTERVENTION_REQUIRED:
                stage_status = "waiting_for_human_intervention"
            elif project.phase == ResearchPhase.REVISION:
                if not job_id:
                    raise InvalidTransitionError("Approved revision batches must run through the asynchronous job endpoint.")
                revision_artifacts, revision_status = self._execute_approved_revision_plan(project)
                produced_artifacts += revision_artifacts
                stage_status = revision_status
                revision_required = project.phase == ResearchPhase.HUMAN_REVISION_REVIEW
            elif project.phase == ResearchPhase.EXECUTION_WAITING:
                if project.execution_capability == "INTERNAL_EXECUTABLE" and project.executor_binding:
                    self._transition_event(project, "execution_tool_available")
                elif project.planning_only:
                    self._transition_event(project, "planning_only")
                else:
                    stage_status = "waiting_for_execution_or_data"
                    project.human_actions_required.append(
                        "Provide data or connect an approved execution backend; no results were generated."
                    )
            elif project.phase == ResearchPhase.EXECUTION:
                if project.execution_capability != "INTERNAL_EXECUTABLE" or not project.executor_binding:
                    raise InvalidTransitionError("No approved internal executor is bound to this project.")
                produced_artifacts += self._run_bound_internal_executor(project)
                self.state_machine.transition(project, "success")
            elif project.phase == ResearchPhase.DATA_ANALYSIS:
                analysis_artifacts = self._record_execution_analysis(project)
                if analysis_artifacts:
                    produced_artifacts += analysis_artifacts
                    self.state_machine.transition(project, "next")
                else:
                    stage_status = "waiting_for_analysis_backend"
                    project.human_actions_required.append(
                        "A dataset is registered, but no verifiable execution result is available; no results were generated."
                    )
            elif project.phase == ResearchPhase.CRITICAL_REVIEW:
                review = self._run_agent(project, SkepticalReviewerAgent)
                self._refresh_quality_metrics(project)
                gated_review = apply_reviewer_quality_gates(
                    self._prepare_review_for_project(project, review.output), project.quality_metrics
                )
                gated_review = self._converge_verified_revision_review(project, gated_review)
                project.reviews.append(gated_review)
                review = AgentRun(
                    output=gated_review,
                    metadata=review.metadata,
                    tool_names=review.tool_names,
                    auxiliary=review.auxiliary,
                )
                produced_artifacts += self._record_agent_output(project, previous_phase, review, "critical_review")
                review_decision = gated_review.decision
                blocking_issues = gated_review.blocking_issues
                review_flow = self._apply_review_decision(project, gated_review)
                revision_required = review_flow["revision_required"]
                max_revision_exhausted = review_flow["max_revision_exhausted"]
            elif project.phase == ResearchPhase.SYNTHESIS:
                run = self._run_agent(project, ScientificSynthesizerAgent)
                project.conclusion = run.output
                self._refresh_quality_metrics(project)
                produced_artifacts += self._record_agent_output(project, previous_phase, run, "research_plan_synthesis")
                self.state_machine.transition(project, "next")
                produced_artifacts += self._write_research_plan(project)
            else:
                raise InvalidTransitionError(f"No stage handler for phase {project.phase.value}")
            project.active_job_id = None
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
                visibility="user",
                display_key=f"{previous_phase.value.lower()}_failed",
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
            project.active_job_id = None
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

    def approve_search_plan(
        self,
        project_id: str,
        queries: list[str] | None = None,
        auto_approve_future: bool = False,
    ) -> ResearchProject:
        project = self.get_project(project_id)
        if project.phase != ResearchPhase.SEARCH_PLAN_REVIEW:
            raise InvalidTransitionError("Search plan can only be approved from SEARCH_PLAN_REVIEW.")
        checkpoint = project.background_research_checkpoint
        if checkpoint.search_plan is None:
            raise AIScientistError("No search plan is available for review.")
        plan = checkpoint.search_plan
        if queries is not None:
            plan = type(plan).model_validate(
                {
                    **plan.model_dump(mode="json"),
                    "queries": queries,
                    "approved_at": None,
                    "approved_by": None,
                }
            )
        validate_search_plan_binding(project, plan)
        relevance_status, relevance_note = deterministic_plan_relevance(project, plan)
        if relevance_status == "irrelevant":
            checkpoint.search_plan = plan.model_copy(
                update={"relevance_status": relevance_status, "relevance_note": relevance_note}
            )
            self.store.save(project)
            raise InvalidTransitionError(
                "The reviewed search plan still does not cover the active research question."
            )
        plan = plan.model_copy(
            update={
                "relevance_status": relevance_status,
                "relevance_note": relevance_note,
                "approved_at": utc_now(),
                "approved_by": "human",
            }
        )
        checkpoint.search_plan = plan
        checkpoint.search_plan_approved = True
        checkpoint.awaiting_search_plan_review = False
        project.auto_approve_search_plan = auto_approve_future
        if project.search_plan_history and project.search_plan_history[-1].search_plan_id == plan.search_plan_id:
            project.search_plan_history[-1] = plan
        project.phase = ResearchPhase.BACKGROUND_RESEARCH
        self._save_search_checkpoint(project, checkpoint)
        self._append_event(
            project,
            completed_event(
                project.project_id,
                ResearchPhase.SEARCH_PLAN_REVIEW,
                "human_reviewer",
                status="search_plan_approved",
                visibility="user",
                display_key=f"search_plan_approved_v{plan.version}",
                display_markdown="检索方案已由研究者确认，下一阶段将执行有界联网检索。",
            ),
        )
        self.store.save(project)
        return project

    def regenerate_search_plan(self, project_id: str) -> ResearchProject:
        project = self.get_project(project_id)
        if project.phase not in {ResearchPhase.SEARCH_PLAN_REVIEW, ResearchPhase.HUMAN_SOURCE_REVIEW}:
            raise InvalidTransitionError("Search can only be regenerated from a source-review phase.")
        project.background_research_checkpoint = BackgroundResearchCheckpoint()
        project.curated_sources = []
        project.phase = ResearchPhase.BACKGROUND_RESEARCH
        self._append_event(
            project,
            completed_event(
                project.project_id,
                ResearchPhase.SEARCH_PLAN_REVIEW,
                "human_reviewer",
                status="search_plan_regeneration_requested",
                visibility="user",
                display_key=f"search_plan_regenerate_{len(project.search_plan_history)}",
                display_markdown="研究者要求重新生成检索方案；既有计划历史和来源排除理由已保留。",
            ),
        )
        self.store.save(project)
        return project

    def add_human_sources(self, project_id: str, entries: list[str]) -> ResearchProject:
        project = self.get_project(project_id)
        if project.phase != ResearchPhase.HUMAN_SOURCE_REVIEW:
            raise InvalidTransitionError("Sources can only be added during HUMAN_SOURCE_REVIEW.")
        checkpoint = project.background_research_checkpoint
        existing = {item.url or item.title.lower() for item in checkpoint.candidates}
        added = []
        for index, entry in enumerate(entries, start=len(checkpoint.candidates) + 1):
            if not entry.strip():
                continue
            candidate = parse_human_source(entry, index)
            key = candidate.url or candidate.title.lower()
            if key in existing:
                continue
            existing.add(key)
            added.append(candidate)
        checkpoint.candidates.extend(enrich_candidates(project, added))
        if project.source_candidate_collections:
            project.source_candidate_collections[-1].candidates = list(checkpoint.candidates)
        self._save_search_checkpoint(project, checkpoint)
        self.store.save(project)
        return project

    def submit_source_selection(
        self,
        project_id: str,
        decisions: list[SourceDecisionInput],
        selection_note: str = "",
    ) -> ResearchProject:
        project = self.get_project(project_id)
        if project.phase != ResearchPhase.HUMAN_SOURCE_REVIEW:
            raise InvalidTransitionError("Source selection is only allowed during HUMAN_SOURCE_REVIEW.")
        checkpoint = project.background_research_checkpoint
        validate_checkpoint_binding(project)
        candidates = {item.candidate_id: item for item in checkpoint.candidates}
        unknown = [item.candidate_id for item in decisions if item.candidate_id not in candidates]
        if unknown:
            raise ValueError(f"Unknown candidate IDs: {', '.join(unknown)}")
        curated = [
            CuratedSource(
                candidate_id=item.candidate_id,
                decision=item.decision,
                decided_by="human",
                human_note=item.note,
                rejection_reason=item.rejection_reason,
            )
            for item in decisions
        ]
        decided_ids = {item.candidate_id for item in curated}
        curated.extend(
            CuratedSource(candidate_id=candidate_id, decision="defer", decided_by="human")
            for candidate_id in candidates
            if candidate_id not in decided_ids
        )
        kept_ids = [item.candidate_id for item in curated if item.decision == "keep"]
        minimum = max(1, int(os.getenv("AI_SCIENTIST_MIN_CURATED_SOURCES", "1")))
        if len(kept_ids) < minimum:
            raise ValueError(f"At least {minimum} curated source(s) must be kept before continuing.")
        human_ids = [candidate_id for candidate_id in kept_ids if candidates[candidate_id].human_provided]
        snapshot = SourceSelectionSnapshot(
            project_id=project.project_id,
            iteration=project.iteration,
            research_question_version=research_question_version(project),
            search_plan_version=checkpoint.search_plan.version if checkpoint.search_plan else 1,
            kept_candidate_ids=kept_ids,
            rejected_candidate_ids=[item.candidate_id for item in curated if item.decision == "reject"],
            deferred_candidate_ids=[item.candidate_id for item in curated if item.decision == "defer"],
            human_added_source_ids=human_ids,
            decisions=curated,
            selection_note=selection_note,
        )
        project.curated_sources.extend(curated)
        project.source_selection_snapshots.append(snapshot)
        project.source_review_feedback = summarize_source_feedback(curated)
        checkpoint.selected_candidates = [
            candidates[candidate_id].model_copy(
                update={"extraction_status": "pending", "extracted_text": "", "extraction_error": ""}
            )
            for candidate_id in kept_ids
        ]
        checkpoint.source_selection_id = snapshot.selection_id
        checkpoint.source_selection_completed = True
        checkpoint.awaiting_source_review = False
        checkpoint.extraction_completed = False
        project.phase = ResearchPhase.BACKGROUND_RESEARCH
        record = self.artifacts.save_json(
            project.project_id,
            "source_selection_snapshot",
            snapshot.model_dump(mode="json"),
            "human",
        )
        project.artifacts.append(record)
        self._save_search_checkpoint(project, checkpoint)
        self._append_event(
            project,
            completed_event(
                project.project_id,
                ResearchPhase.HUMAN_SOURCE_REVIEW,
                "human_reviewer",
                status="source_selection_submitted",
                visibility="user",
                display_key=f"source_selection_{snapshot.selection_id}",
                display_markdown=(
                    f"研究者保留{len(snapshot.kept_candidate_ids)}个来源，排除"
                    f"{len(snapshot.rejected_candidate_ids)}个，暂缓{len(snapshot.deferred_candidate_ids)}个。"
                ),
            ),
        )
        self.store.save(project)
        return project

    def register_research_asset(
        self,
        project_id: str,
        filename: str,
        content_type: str,
        content: bytes,
        purpose: str = "reference",
        description: str = "",
        upload_context: str = "project_workspace",
        asset_role: str = "research_material",
        research_round: int | None = None,
        source: str = "user_upload",
    ) -> ResearchProject:
        project = self.get_project(project_id)
        safe_name = Path(filename).name
        suffix = Path(safe_name).suffix.lower()
        if not safe_name or suffix not in RESEARCH_ASSET_SUFFIXES:
            supported = ", ".join(sorted(RESEARCH_ASSET_SUFFIXES))
            raise ValueError(f"Unsupported research asset type. Supported extensions: {supported}.")
        if purpose not in RESEARCH_ASSET_PURPOSES:
            raise ValueError(f"Unsupported research asset purpose: {purpose}.")
        if upload_context not in RESEARCH_ASSET_UPLOAD_CONTEXTS:
            raise ValueError(f"Unsupported research asset upload context: {upload_context}.")
        if asset_role not in {"research_material", "experimental_result"}:
            raise ValueError(f"Unsupported research asset role: {asset_role}.")
        if len(safe_name) > 255:
            raise ValueError("Research asset filename must be 255 characters or fewer.")
        if len(description) > 2000:
            raise ValueError("Research asset description must be 2000 characters or fewer.")
        maximum_bytes = max(1, int(os.getenv("AI_SCIENTIST_MAX_ASSET_BYTES", str(25 * 1024 * 1024))))
        if not content:
            raise ValueError("Uploaded research asset is empty.")
        if len(content) > maximum_bytes:
            raise ValueError(
                f"Uploaded research asset exceeds the {maximum_bytes // (1024 * 1024)} MB limit."
            )
        directory = self.store.project_dir(project_id) / "assets"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{new_id('asset_file')}{suffix}"
        target.write_bytes(content)
        asset = ResearchAsset(
            filename=safe_name,
            content_type=content_type,
            saved_path=str(target.relative_to(self.store.project_dir(project_id))),
            size_bytes=len(content),
            purpose=purpose,
            asset_role=asset_role,
            research_round=research_round,
            source=source.strip() or "user_upload",
            description=description.strip(),
            upload_context=upload_context,
            parsing_status="registered_only",
        )
        project.research_assets.append(asset)
        self._append_event(
            project,
            completed_event(
                project.project_id,
                project.phase,
                "human_reviewer",
                status="research_asset_registered",
                visibility="user",
                display_key=f"research_asset_{asset.asset_id}",
                display_markdown=(
                    f"已登记{'数据文件' if purpose == 'data' else '参考资料'}：{safe_name}。"
                    "系统将生成有边界、可审计的本地解析结果。"
                ),
            ),
        )
        self.store.save(project)
        parse_on_upload = os.getenv("AI_SCIENTIST_PARSE_ASSETS_ON_UPLOAD", "true").strip().lower()
        if parse_on_upload in {"1", "true", "yes", "on"}:
            return self.parse_research_asset(project_id, asset.asset_id)
        return project

    def delete_research_asset(self, project_id: str, asset_id: str) -> ResearchProject:
        """Delete an unused mistaken upload while preserving the audit event."""

        project = self.get_project(project_id)
        asset = next((item for item in project.research_assets if item.asset_id == asset_id), None)
        if asset is None:
            raise ResearchAssetNotFoundError(f"Research asset not found: {asset_id}")
        if asset.used_by_agents:
            raise ValueError("Research assets already used by agents cannot be deleted; retain provenance.")
        project_directory = self.store.project_dir(project_id).resolve()
        target = (project_directory / asset.saved_path).resolve()
        if not target.is_relative_to(project_directory):
            raise ResearchAssetNotFoundError(f"Research asset path is unsafe: {asset_id}")
        if target.is_file():
            target.unlink()
        project.research_assets = [item for item in project.research_assets if item.asset_id != asset_id]
        self._append_event(
            project,
            completed_event(
                project.project_id,
                project.phase,
                "human_reviewer",
                status="research_asset_deleted",
                visibility="user",
                display_key=f"research_asset_deleted_{asset.asset_id}",
                display_markdown=f"已删除尚未被科研角色使用的误上传文件：{asset.filename}。",
            ),
        )
        self.store.save(project)
        return project

    def parse_research_asset(self, project_id: str, asset_id: str) -> ResearchProject:
        """Parse one registered file locally and persist an auditable parse artifact."""

        project = self.get_project(project_id)
        asset = next((item for item in project.research_assets if item.asset_id == asset_id), None)
        if asset is None:
            raise ResearchAssetNotFoundError(f"Research asset not found: {asset_id}")
        _, target = self.get_research_asset(project_id, asset_id)
        asset.parsing_status = "parsing"
        asset.parse_error = ""
        self.store.save(project)
        try:
            parsed = parse_local_research_asset(target)
            record = self.artifacts.save_json(
                project.project_id,
                "parsed_research_asset",
                {
                    "asset_id": asset.asset_id,
                    "filename": asset.filename,
                    "purpose": asset.purpose,
                    "asset_role": asset.asset_role,
                    "research_round": asset.research_round,
                    "source": asset.source,
                    "description": asset.description,
                    "parsed_content": parsed.model_dump(mode="json"),
                },
                "local_document_parser",
            )
            project.artifacts.append(record)
            asset.parsed_content = parsed
            asset.parsed_artifact_id = record.artifact_id
            asset.parsing_status = "parsed"
            self._refresh_internal_data_executor_binding(project)
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    project.phase,
                    "local_document_parser",
                    status="research_asset_parsed",
                    visibility="user",
                    display_key=f"research_asset_parsed_{asset.asset_id}",
                    display_markdown=(
                        f"已解析项目文件：{asset.filename}。解析结果将在后续研究阶段作为带来源标识的材料使用。"
                    ),
                    output_artifact_ids=[record.artifact_id],
                ),
            )
        except Exception as exc:  # noqa: BLE001 - preserve the raw file and expose a retryable state
            message = str(exc).strip() or exc.__class__.__name__
            api_key = os.getenv("DASHSCOPE_API_KEY", "")
            if api_key:
                message = message.replace(api_key, "[REDACTED_API_KEY]")
            asset.parsing_status = "failed"
            asset.parsed_content = None
            asset.parsed_artifact_id = None
            asset.parse_error = f"{exc.__class__.__name__}: {message}"[:1000]
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    project.phase,
                    "local_document_parser",
                    status="research_asset_parse_failed",
                    visibility="user",
                    display_key=f"research_asset_parse_failed_{asset.asset_id}",
                    display_markdown=f"项目文件解析失败，可稍后重试：{asset.filename}。",
                ),
            )
        self.store.save(project)
        return project

    def get_research_asset(self, project_id: str, asset_id: str) -> tuple[ResearchAsset, Path]:
        """Resolve one project asset while preventing paths from escaping its project directory."""

        project = self.get_project(project_id)
        asset = next((item for item in project.research_assets if item.asset_id == asset_id), None)
        if asset is None:
            raise ResearchAssetNotFoundError(f"Research asset not found: {asset_id}")
        project_directory = self.store.project_dir(project_id).resolve()
        target = (project_directory / asset.saved_path).resolve()
        if not target.is_relative_to(project_directory) or not target.is_file():
            raise ResearchAssetNotFoundError(f"Research asset file is unavailable: {asset_id}")
        return asset, target

    def approve_project(
        self,
        project_id: str,
        acknowledged: bool = False,
        expected_versions: dict[str, int | None] | None = None,
    ) -> ResearchProject:
        project = self.get_project(project_id)
        if project.phase != ResearchPhase.HUMAN_APPROVAL:
            raise InvalidTransitionError("Project can only be approved from HUMAN_APPROVAL.")
        package = self._ensure_review_package(project)
        latest_review = project.reviews[-1] if project.reviews else None
        if not acknowledged:
            raise InvalidTransitionError("Human approval requires explicit reviewer acknowledgment.")
        if latest_review is None or latest_review.decision != "approve":
            raise InvalidTransitionError("The independent reviewer has not approved this research package.")
        if latest_review.blocking_issues or package.blocking_issue_count:
            raise InvalidTransitionError("Blocking review issues must be resolved before approval.")
        if not package.ready_for_approval:
            raise InvalidTransitionError("The review package is incomplete and cannot be approved.")
        current_versions = self._review_artifact_versions(project)
        submitted_versions = expected_versions or package.artifact_versions
        if submitted_versions != package.artifact_versions or current_versions != package.artifact_versions:
            project.approval_status = "stale"
            self.store.save(project)
            raise InvalidTransitionError("The review package is stale because active artifact versions changed.")
        project.human_approval_history.append(
            HumanApprovalRecord(
                project_id=project.project_id,
                package_id=package.package_id,
                approved_versions=dict(package.artifact_versions),
                acknowledgment=True,
            )
        )
        project.approval_valid_for_versions = dict(package.artifact_versions)
        project.approval_status = "valid"
        self._refresh_internal_data_executor_binding(project)
        approval_next_step = (
            "项目将进入内部确定性执行阶段。"
            if project.execution_capability == "INTERNAL_EXECUTABLE"
            else "项目将等待外部实验结果。"
            if project.execution_capability == "EXTERNAL_EXECUTION_REQUIRED"
            else "项目将进入科学综合阶段。"
        )
        self._append_event(
            project,
            completed_event(
                project.project_id,
                ResearchPhase.HUMAN_APPROVAL,
                "human_reviewer",
                status="approved",
                visibility="user",
                display_key="human_approval_granted",
                display_markdown=f"人工审查已确认当前版本的研究方案，{approval_next_step}",
            ),
        )
        self._transition_event(project, "approve")
        if project.planning_only and project.phase == ResearchPhase.EXECUTION_WAITING:
            self._transition_event(project, "planning_only")
        self.store.save(project)
        return project

    def request_revision(self, project_id: str, target_phase: str, feedback: str) -> ResearchProject:
        project = self.get_project(project_id)
        if project.phase != ResearchPhase.HUMAN_APPROVAL:
            raise InvalidTransitionError("Revision can only be requested from HUMAN_APPROVAL.")
        if target_phase not in {"question", "evidence", "hypothesis", "method", "design", "analysis", "reproducibility"}:
            raise ValueError(f"Unsupported revision target: {target_phase}")
        if not feedback.strip():
            raise ValueError("Revision feedback must describe the requested change.")
        project.revision_feedback.append(feedback)
        project.human_revision_history.append(
            HumanRevisionRecord(
                project_id=project.project_id,
                target=target_phase,
                feedback=feedback,
                artifact_versions=self._review_artifact_versions(project),
            )
        )
        project.pending_revision_target = target_phase
        self._invalidate_approval(project, f"人工审查要求修订：{feedback}")
        self._targeted_rollback(project, target_phase, feedback, changed_fields=[])
        self.store.save(project)
        return project

    def defer_approval(self, project_id: str, reason: str = "") -> ResearchProject:
        """Record a non-terminal human decision without invoking models or revisions."""

        project = self.get_project(project_id)
        if project.phase != ResearchPhase.HUMAN_APPROVAL:
            raise InvalidTransitionError("Approval can only be deferred from HUMAN_APPROVAL.")
        self._ensure_review_package(project)
        project.approval_status = "deferred"
        self._append_event(
            project,
            completed_event(
                project.project_id,
                ResearchPhase.HUMAN_APPROVAL,
                "human_reviewer",
                status="approval_deferred",
                visibility="user",
                display_key="human_approval_deferred",
                display_markdown=f"人工审查选择暂不批准。{reason.strip() or '项目保留在当前阶段。'}",
            ),
        )
        self.store.save(project)
        return project

    def get_review_package(self, project_id: str) -> ReviewPackage:
        project = self.get_project(project_id)
        if project.phase != ResearchPhase.HUMAN_APPROVAL:
            raise InvalidTransitionError("Review package is available only during HUMAN_APPROVAL.")
        package = self._ensure_review_package(project)
        self.store.save(project)
        return package

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
        elif project.phase == ResearchPhase.HUMAN_INTERVENTION_REQUIRED:
            project.phase = ResearchPhase.CRITICAL_REVIEW
            project.revision_issues = []
            project.current_revision_action = None
            project.pending_revision_actions = []
            project.stage_messages.append(
                "人工已补充数据清单；项目将保留既有修订历史并重新进行独立复审。"
            )
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    ResearchPhase.HUMAN_INTERVENTION_REQUIRED,
                    "human_researcher",
                    status="data_provided_for_re_review",
                    visibility="user",
                    display_key=f"data_provided_for_re_review_{record.artifact_id}",
                    output_artifact_ids=[record.artifact_id],
                    display_markdown="已登记补充数据，下一阶段将重新进行独立复审。",
                ),
            )
        self.store.save(project)
        return project

    def run_registered_dataset_tools(self, project_id: str) -> ResearchProject:
        """Explicitly execute the safe dataset toolchain for an approved or completed project."""

        project = self.get_project(project_id)
        if project.phase not in {
            ResearchPhase.EXECUTION_WAITING,
            ResearchPhase.DATA_ANALYSIS,
            ResearchPhase.COMPLETED,
        }:
            raise InvalidTransitionError(
                "Registered dataset tools can run only while waiting for execution, during data analysis, or after completion."
            )
        if project.planning_only:
            raise InvalidTransitionError("Planning-only projects cannot execute dataset tools.")
        previous_phase = project.phase
        self._refresh_internal_data_executor_binding(project)
        if project.executor_binding != "deterministic_data_analysis_v1":
            raise InvalidTransitionError(
                "No parsed tabular data asset is available for the selected research mode."
            )
        previous_claim_ids = set(project.internal_execution_summary.get("generated_claim_ids") or [])
        previous_evidence_id = project.internal_execution_summary.get("execution_evidence_id")
        if previous_claim_ids:
            project.claims = [item for item in project.claims if item.claim_id not in previous_claim_ids]
        if previous_evidence_id:
            project.evidence = [item for item in project.evidence if item.evidence_id != previous_evidence_id]
        project.conclusion = None
        project.phase = ResearchPhase.EXECUTION
        self._run_deterministic_data_analysis(project)
        project.phase = ResearchPhase.DATA_ANALYSIS
        self._record_execution_analysis(project)
        project.phase = ResearchPhase.CRITICAL_REVIEW
        self._append_event(
            project,
            completed_event(
                project.project_id,
                previous_phase,
                "human_researcher",
                status="registered_dataset_tools_executed",
                visibility="user",
                display_key=f"registered_dataset_tools_{len(project.artifacts)}",
                display_markdown="已使用项目内白名单工具复现数据分析；下一阶段将独立复审执行结果。",
            ),
        )
        self.store.save(project)
        return project

    def _run_bound_internal_executor(self, project: ResearchProject) -> list[str]:
        """Run only the explicit, allowlisted executor bound at project creation."""

        if project.executor_binding == "deterministic_data_analysis_v1":
            return self._run_deterministic_data_analysis(project)
        if project.executor_binding != "damped_oscillator_v1":
            raise InvalidTransitionError(f"Unsupported executor binding: {project.executor_binding}")
        compatible_assets = [
            item
            for item in project.research_assets
            if item.purpose == "data" and Path(item.filename).suffix.lower() == ".csv"
        ]
        if not compatible_assets:
            raise InvalidTransitionError(
                "The damped-oscillator executor requires a project observation CSV."
            )
        observation_asset = compatible_assets[-1]
        _, observation_path = self.get_research_asset(project.project_id, observation_asset.asset_id)
        known_truth = (
            {"damping": 0.173, "omega": 2.37}
            if observation_asset.source == "bundled_example"
            else None
        )
        execution_root = self.store.project_dir(project.project_id) / "internal_execution" / "damped_oscillator"
        state = CompetitionRuntime(execution_root).run_flagship(
            project.reproducibility_seed or 20260831,
            observations_path=observation_path,
            ground_truth=known_truth,
        )
        if state.status != "complete" or len(state.executions) < 3 or not state.iterations:
            raise AIScientistError(f"Internal deterministic execution failed with status {state.status}.")
        round_1 = state.executions[1]
        round_2 = state.executions[2]
        feedback_signals = [
            item.model_dump(mode="json")
            for iteration in state.iterations
            for item in iteration.feedback_signals
        ]
        adjustments = [
            item.model_dump(mode="json")
            for iteration in state.iterations
            for item in iteration.adjustments
        ]
        project.internal_execution_summary = {
            "executor_binding": project.executor_binding,
            "run_id": state.run_id,
            "status": state.status,
            "seed": state.seed,
            "observation_asset_id": observation_asset.asset_id,
            "root_directory": str(execution_root.relative_to(self.store.project_dir(project.project_id))),
            "round_1": round_1.model_dump(mode="json"),
            "feedback_signals": feedback_signals,
            "plan_adjustments": adjustments,
            "round_2": round_2.model_dump(mode="json"),
            "comparison": state.comparison,
            "iteration_records": [item.model_dump(mode="json") for item in state.iterations],
        }
        record = self.artifacts.save_json(
            project.project_id,
            "internal_execution_run",
            state.model_dump(mode="json"),
            project.executor_binding,
        )
        project.artifacts.append(record)
        if project.executor_binding not in observation_asset.used_by_agents:
            observation_asset.used_by_agents.append(project.executor_binding)
        self._append_event(
            project,
            completed_event(
                project.project_id,
                ResearchPhase.EXECUTION,
                project.executor_binding,
                status="internal_execution_completed",
                visibility="user",
                display_key=f"internal_execution_{state.run_id}",
                output_artifact_ids=[record.artifact_id],
                display_markdown=(
                    "内部确定性执行器已完成 Round 1、FeedbackSignal、PlanAdjustment、"
                    "Round 2 与 comparison。"
                ),
            ),
        )
        return [record.artifact_id]

    def _refresh_internal_data_executor_binding(self, project: ResearchProject) -> None:
        """Bind the safe dataset executor only when mode, data, and user intent permit it."""

        if project.executor_binding == "damped_oscillator_v1" or project.planning_only:
            return
        eligible_modes = {
            ResearchMode.DATA_ANALYSIS,
            ResearchMode.OBSERVATIONAL,
            ResearchMode.MIXED_METHODS,
        }
        compatible = [
            item
            for item in project.research_assets
            if item.purpose == "data"
            and item.parsing_status == "parsed"
            and Path(item.filename).suffix.lower() in {".csv", ".tsv", ".json", ".xlsx", ".xls"}
        ]
        if project.research_mode in eligible_modes and compatible:
            project.executor_binding = "deterministic_data_analysis_v1"
            project.execution_capability = "INTERNAL_EXECUTABLE"
            if project.executor_binding not in project.available_tools:
                project.available_tools.append(project.executor_binding)

    @staticmethod
    def _analysis_preferred_terms(project: ResearchProject) -> str:
        parts = [project.objective]
        if project.question:
            parts.extend([
                project.question.normalized_question,
                " ".join(project.question.operational_definitions),
            ])
        if project.analysis_plan:
            parts.extend([
                " ".join(project.analysis_plan.input_data),
                " ".join(project.analysis_plan.metrics),
                " ".join(project.analysis_plan.statistical_methods),
            ])
        return " ".join(parts)

    def _run_deterministic_data_analysis(self, project: ResearchProject) -> list[str]:
        compatible = [
            item
            for item in project.research_assets
            if item.purpose == "data"
            and item.parsing_status == "parsed"
            and Path(item.filename).suffix.lower() in {".csv", ".tsv", ".json", ".xlsx", ".xls"}
        ]
        if not compatible:
            raise InvalidTransitionError("The deterministic data analyzer requires a parsed tabular data asset.")
        asset = compatible[-1]
        _, dataset_path = self.get_research_asset(project.project_id, asset.asset_id)
        # Resolve both sides before deriving the project-relative path.  The
        # production store commonly uses a relative root (data/research_projects),
        # while get_research_asset deliberately returns a resolved safe path.
        project_root = self.store.project_dir(project.project_id).resolve()
        relative_dataset_path = dataset_path.relative_to(project_root).as_posix()
        adapter = ExecutionAdapter(project_root)
        seed = project.reproducibility_seed or 0
        requests = adapter.default_dataset_requests(
            relative_dataset_path,
            "internal_execution/deterministic_data_analysis",
            seed=seed,
            preferred_terms=self._analysis_preferred_terms(project),
        )
        results = [adapter.execute(request) for request in requests]
        failed = [item for item in results if item["status"] != "success"]
        if failed:
            failures = "; ".join(
                f"{item['operation']}: {item.get('failure_reason') or item['status']}" for item in failed
            )
            raise AIScientistError(f"Deterministic dataset analysis failed: {failures}")
        project.internal_execution_summary = {
            "executor_binding": project.executor_binding,
            "status": "complete",
            "seed": seed,
            "dataset_asset_id": asset.asset_id,
            "dataset_filename": asset.filename,
            "dataset_source": asset.source,
            "dataset_content_sha256": asset.parsed_content.content_sha256 if asset.parsed_content else "",
            "root_directory": "internal_execution/deterministic_data_analysis",
            "operations": results,
            "operation_count": len(results),
            "arbitrary_code_execution": False,
        }
        execution_evidence = EvidenceItem(
            title=f"Project-internal deterministic analysis of {asset.filename}",
            source_type="dataset",
            source_asset_id=asset.asset_id,
            summary=(
                f"The allowlisted project executor completed {len(results)} operations against input "
                f"SHA-256 {project.internal_execution_summary['dataset_content_sha256']}."
            ),
            extracted_claims=[],
            reliability="project-internal deterministic execution",
            relevance="direct analysis of the approved dataset",
            limitations=[
                "Results apply only to the registered dataset and recorded parameters.",
                "Descriptive associations do not establish causality.",
            ],
            source_level="A",
            is_primary_source=True,
            verified=True,
            verification_status="verified",
            verification_method="none",
            verification_note="Verified by input checksum, fixed operation whitelist, saved parameters, and output checksums.",
            reliability_score=1.0,
            relevance_score=1.0,
        )
        project.evidence.append(execution_evidence)
        generated_claims: list[Claim] = []
        for result in results:
            if result["operation"] == "inspect_dataset":
                metrics = result.get("metrics") or {}
                generated_claims.append(Claim(
                    statement=(
                        f"项目内工具读取了 {metrics.get('rows')} 行、{metrics.get('columns')} 列数据。"
                        if any("\u4e00" <= char <= "\u9fff" for char in project.objective)
                        else f"The project tool read {metrics.get('rows')} rows and {metrics.get('columns')} columns."
                    ),
                    claim_type="observation",
                    supporting_evidence_ids=[execution_evidence.evidence_id],
                    confidence=1.0,
                    limitations=["Limited to the recorded input checksum."],
                    status="supported",
                ))
            if result["operation"] == "correlation":
                for pair in (result.get("metrics") or {}).get("pairs", [])[:20]:
                    interval = pair.get("ci95_fisher_z")
                    interval_text = (
                        f", 95% CI [{interval[0]:.4f}, {interval[1]:.4f}]" if interval else ""
                    )
                    generated_claims.append(Claim(
                        statement=(
                            f"项目内确定性分析：{pair['group']} 组的 {pair['x']} 与 {pair['y']} "
                            f"{pair['method']} 相关系数为 {pair['coefficient']:.4f}（n={pair['n']}）{interval_text}。"
                            if any("\u4e00" <= char <= "\u9fff" for char in project.objective)
                            else (
                                f"Project-internal deterministic analysis: {pair['group']} {pair['x']} versus "
                                f"{pair['y']} {pair['method']} correlation was {pair['coefficient']:.4f} "
                                f"(n={pair['n']}){interval_text}."
                            )
                        ),
                        claim_type="observation",
                        supporting_evidence_ids=[execution_evidence.evidence_id],
                        confidence=0.99,
                        assumptions=["Recorded columns were interpreted as numeric measurements."],
                        limitations=["Association is not causation."],
                        status="supported",
                    ))
        execution_evidence.extracted_claims = [item.statement for item in generated_claims]
        project.claims.extend(generated_claims)
        project.internal_execution_summary["execution_evidence_id"] = execution_evidence.evidence_id
        project.internal_execution_summary["generated_claim_ids"] = [item.claim_id for item in generated_claims]
        self._refresh_quality_metrics(project)
        record = self.artifacts.save_json(
            project.project_id,
            "internal_data_analysis_run",
            project.internal_execution_summary,
            project.executor_binding,
        )
        project.artifacts.append(record)
        if project.executor_binding not in asset.used_by_agents:
            asset.used_by_agents.append(project.executor_binding)
        self._append_event(
            project,
            completed_event(
                project.project_id,
                ResearchPhase.EXECUTION,
                project.executor_binding,
                status="internal_data_analysis_completed",
                visibility="user",
                display_key=f"internal_data_analysis_{record.artifact_id}",
                output_artifact_ids=[record.artifact_id],
                display_markdown=(
                    f"项目内白名单工具已对 {asset.filename} 完成 {len(results)} 项确定性分析；"
                    "输入哈希、参数、软件版本和输出校验和均已保存。"
                ),
            ),
        )
        return [record.artifact_id]

    def _record_execution_analysis(self, project: ResearchProject) -> list[str]:
        """Persist analysis provenance without inventing results for external projects."""

        if project.internal_execution_summary:
            payload = {
                "analysis_source": "internal_deterministic_executor",
                "executor_binding": project.executor_binding,
                "comparison": project.internal_execution_summary.get("comparison", {}),
                "feedback_signals": project.internal_execution_summary.get("feedback_signals", []),
                "plan_adjustments": project.internal_execution_summary.get("plan_adjustments", []),
                "dataset_asset_id": project.internal_execution_summary.get("dataset_asset_id"),
                "dataset_content_sha256": project.internal_execution_summary.get("dataset_content_sha256"),
                "operations": project.internal_execution_summary.get("operations", []),
            }
            status = "internal_execution_analyzed"
            markdown = "已从真实内部执行结果形成可审计分析，并将进入独立复审。"
        else:
            manifests = [item for item in project.artifacts if item.artifact_type == "provided_data_manifest"]
            experimental_assets = [
                item for item in project.research_assets if item.asset_role == "experimental_result"
            ]
            if not manifests and not experimental_assets:
                return []
            payload = {
                "analysis_source": "researcher_provided_external_result",
                "provided_manifest_artifact_ids": [item.artifact_id for item in manifests],
                "experimental_assets": [
                    {
                        "asset_id": item.asset_id,
                        "filename": item.filename,
                        "research_round": item.research_round,
                        "source": item.source,
                        "parsed_artifact_id": item.parsed_artifact_id,
                    }
                    for item in experimental_assets
                ],
                "generated_metrics": {},
                "handling_rule": "No execution metrics were generated or inferred by the system.",
            }
            status = "external_result_registered_for_review"
            markdown = "已登记研究者提供的真实外部结果；系统未生成实验数值，下一阶段将独立复审。"
        record = self.artifacts.save_json(
            project.project_id,
            "execution_analysis",
            payload,
            "orchestrator",
        )
        project.artifacts.append(record)
        self._append_event(
            project,
            completed_event(
                project.project_id,
                ResearchPhase.DATA_ANALYSIS,
                "orchestrator",
                status=status,
                visibility="user",
                display_key=f"{status}_{record.artifact_id}",
                output_artifact_ids=[record.artifact_id],
                display_markdown=markdown,
            ),
        )
        return [record.artifact_id]

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
        project = self.store.load(project_id)
        if self._recover_orphan_revision(project):
            self.store.save(project)
        return project

    def _recover_orphan_revision(self, project: ResearchProject) -> bool:
        changed = False
        active_plan = next(
            (
                item
                for item in project.approved_revision_plans
                if item.revision_plan_id == project.active_revision_plan_id
            ),
            None,
        )
        stale_context_recovered = False
        if active_plan is not None:
            for batch in active_plan.target_batches:
                if batch.issue_snapshots:
                    continue
                matched = [
                    issue for issue in project.revision_issues if issue.issue_id in batch.issue_ids
                ]
                stale_ids = bool(batch.issue_ids) and not matched
                if not matched:
                    matched = [issue for issue in project.revision_issues if issue.target == batch.target]
                if matched:
                    batch.issue_snapshots = [issue.model_copy(deep=True) for issue in matched]
                    batch.completion_criteria = list(
                        dict.fromkeys(
                            criterion
                            for issue in matched
                            for criterion in issue.completion_criteria
                        )
                    )
                    changed = True
                    stale_context_recovered = stale_context_recovered or stale_ids
        if (
            active_plan is not None
            and active_plan.status == "needs_attention"
            and stale_context_recovered
        ):
            for batch in active_plan.target_batches:
                if batch.status == "needs_attention":
                    batch.status = "pending"
                    batch.job_id = None
            active_plan.status = "approved"
            project.current_revision_action = None
            project.active_job_id = None
            project.phase = ResearchPhase.REVISION
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    ResearchPhase.REVISION,
                    "orchestrator",
                    status="revision_context_snapshot_recovered",
                    visibility="user",
                    display_key=(
                        f"revision_cycle_{active_plan.revision_cycle}_context_snapshot_recovered"
                    ),
                    display_markdown=(
                        "检测到旧计划的修订问题 ID 已失效；已从当前审查恢复验收标准快照，"
                        "本轮可直接重新验证。"
                    ),
                ),
            )
            changed = True
        if active_plan is not None and active_plan.status == "in_progress":
            if project.active_job_id and self._job_record_is_active(project.project_id, project.active_job_id):
                return False
            interrupted = [batch for batch in active_plan.target_batches if batch.status == "in_progress"]
            if interrupted:
                for batch in interrupted:
                    batch.status = "pending"
                    batch.job_id = None
                active_plan.status = "approved"
                project.current_revision_action = None
                project.active_job_id = None
                project.phase = ResearchPhase.REVISION
                message = "上一次模型调用在产物生成前中断；已批准的修订计划已保留，可以直接重试。"
                if message not in project.revision_recovery_messages:
                    project.revision_recovery_messages.append(message)
                    project.stage_messages.append(message)
                self._append_event(
                    project,
                    completed_event(
                        project.project_id,
                        ResearchPhase.REVISION,
                        "orchestrator",
                        status="revision_transport_failure_recovered",
                        visibility="user",
                        display_key=(
                            f"revision_cycle_{active_plan.revision_cycle}_transport_failure_recovered"
                        ),
                        display_markdown="模型调用中断，已保留本轮修订计划；可直接重试，不消耗新的修订轮次。",
                    ),
                )
                changed = True
        action = project.current_revision_action
        if action is not None and action.status == "in_progress":
            if project.active_job_id and self._job_record_is_active(project.project_id, project.active_job_id):
                return False
            project.current_revision_action = action.model_copy(update={"status": "pending"})
            project.completed_revision_actions = [
                item.model_copy(update={"status": "failed_verification"})
                if item.status == "completed"
                else item
                for item in project.completed_revision_actions
            ]
            project.pending_revision_actions = []
            project.active_job_id = None
            if project.reviews:
                project.revision_issues = normalize_revision_plan(project.reviews[-1], project.planning_only)
            project.phase = ResearchPhase.HUMAN_REVISION_REVIEW
            project.revision_migration_version = max(project.revision_migration_version, 1)
            message = "检测到旧版自动修订流程未完整结束，已迁移到人工修订审查。"
            if message not in project.revision_recovery_messages:
                project.revision_recovery_messages.append(message)
                project.stage_messages.append(message)
            event = completed_event(
                project.project_id,
                ResearchPhase.HUMAN_REVISION_REVIEW,
                "orchestrator",
                status="orphan_revision_action_recovered",
                visibility="user",
                display_key="orphan_revision_action_recovered_v1",
                display_markdown="上一次修订任务未完整结束，已恢复为可重新审查和执行的状态。",
            )
            self._append_event(project, event)
            changed = True

        if (
            project.revision_migration_version == 1
            and project.phase == ResearchPhase.HUMAN_REVISION_REVIEW
            and project.reviews
        ):
            project.revision_issues = normalize_revision_plan(project.reviews[-1], project.planning_only)
            project.revision_migration_version = 2
            changed = True
        if (
            project.revision_migration_version == 2
            and project.phase == ResearchPhase.HUMAN_REVISION_REVIEW
            and not project.approved_revision_plans
        ):
            project.iteration = max(0, project.iteration - 1)
            project.budget.used_iterations = max(0, project.budget.used_iterations - 1)
            project.revision_migration_version = 3
            changed = True
        if project.revision_migration_version < 5 and project.planning_only:
            for issue in project.revision_issues:
                issue.completion_criteria = normalize_completion_criteria(
                    issue.completion_criteria,
                    planning_only=True,
                )
            project.revision_migration_version = 5
            changed = True
        return changed

    def _job_record_is_active(self, project_id: str, job_id: str) -> bool:
        path = self.store.project_dir(project_id) / "jobs" / f"{job_id}.json"
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return payload.get("status") in {"queued", "running"}

    def list_events(self, project_id: str) -> list[ResearchEvent]:
        self.get_project(project_id)
        return self.store.list_events(project_id)

    def recover_revision_projects(self) -> int:
        """Recover legacy orphan revision states during backend startup."""

        recovered = 0
        for directory in self.store.root.iterdir():
            if not directory.is_dir() or not (directory / "project.json").exists():
                continue
            project = self.store.load(directory.name)
            if self._recover_orphan_revision(project):
                self.store.save(project)
                recovered += 1
        return recovered

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
            self._ensure_review_package(project)
            return {"revision_required": False, "max_revision_exhausted": False}
        if review.decision == "reject":
            self.state_machine.transition(project, "reject")
            return {"revision_required": False, "max_revision_exhausted": False}

        issues = normalize_revision_plan(review, project.planning_only)
        project.revision_issues = issues
        project.pending_revision_actions = []
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
        project.phase = ResearchPhase.HUMAN_REVISION_REVIEW
        project.stage_messages.append(
            "独立审查没有否定当前研究，但提出了需要由您决定如何处理的修订问题。"
        )
        event = completed_event(
            project.project_id,
            ResearchPhase.FEASIBILITY_REVIEW if len(project.reviews) == 1 else ResearchPhase.CRITICAL_REVIEW,
            "skeptical_reviewer",
            job_id=_CURRENT_JOB_ID.get(),
            status="revision_review_required",
            visibility="user",
            display_key=f"revision_cycle_{project.iteration + 1}_review_required",
            display_markdown=(
                f"独立审查提出 {len(issues)} 项问题。系统已暂停自动修订，等待您审核修订计划。"
            ),
        )
        self._append_event(project, event)
        return {"revision_required": True, "max_revision_exhausted": False}

    def _prepare_review_for_project(self, project: ResearchProject, review: ReviewResult) -> ReviewResult:
        """Keep planning defects blocking while moving future execution work out of the gate."""

        if not project.planning_only or review.decision == "reject":
            return review
        issues = normalize_revision_plan(review, planning_only=True)
        plan_blockers = [issue.problem for issue in issues if issue.classification == "plan_blocking"]
        execution = [issue.problem for issue in issues if issue.classification == "execution_prerequisite"]
        project.execution_requirements.extend(execution)
        non_blocking = list(
            dict.fromkeys(
                review.non_blocking_issues
                + [issue.problem for issue in issues if issue.classification in {"non_blocking", "optional"}]
            )
        )
        scores = [
            review.evidence_quality_score,
            review.methodological_validity_score,
            review.feasibility_score,
            review.reproducibility_score,
            review.claim_support_score,
            review.uncertainty_handling_score,
        ]
        update: dict[str, Any] = {
            "blocking_issues": list(dict.fromkeys(plan_blockers)),
            "non_blocking_issues": non_blocking,
        }
        if not plan_blockers and min(scores) >= 6:
            update.update({"decision": "approve", "required_revision_target": "none", "revision_plan": []})
        return review.model_copy(update=update)

    def _converge_verified_revision_review(
        self,
        project: ResearchProject,
        review: ReviewResult,
    ) -> ReviewResult:
        """Bound critical re-review to verified, human-approved revision scope."""

        completed_plans = [item for item in project.approved_revision_plans if item.status == "completed"]
        if not completed_plans:
            return review
        plan = completed_plans[-1]
        verification_by_id = {
            item.verification_id: item for item in project.revision_verifications
        }
        if not plan.target_batches or any(
            batch.status != "completed"
            or not batch.verification_id
            or not verification_by_id.get(batch.verification_id)
            or not verification_by_id[batch.verification_id].overall_passed
            for batch in plan.target_batches
        ):
            return review

        integrity_tokens = (
            "fabricated source",
            "fabricated evidence",
            "fabricated data",
            "falsified",
            "ethical violation",
            "safety violation",
            "fundamentally unresearchable",
            "伪造来源",
            "伪造证据",
            "伪造数据",
            "伦理违规",
            "安全违规",
            "根本不可研究",
        )
        integrity_blockers = [
            issue
            for issue in review.blocking_issues
            if any(token in issue.lower() for token in integrity_tokens)
        ]
        if integrity_blockers or review.decision == "reject":
            return review.model_copy(update={"blocking_issues": integrity_blockers or review.blocking_issues})

        score_updates: dict[str, float] = {}
        targets = {batch.target for batch in plan.target_batches}
        target_score_fields = {
            "question": ["feasibility_score", "uncertainty_handling_score"],
            "evidence": ["evidence_quality_score", "claim_support_score"],
            "hypothesis": ["claim_support_score", "uncertainty_handling_score"],
            "methodology": ["methodological_validity_score", "feasibility_score"],
            "study_design": ["methodological_validity_score", "feasibility_score"],
            "analysis_plan": [
                "methodological_validity_score",
                "feasibility_score",
                "reproducibility_score",
                "uncertainty_handling_score",
            ],
            "reproducibility_plan": ["reproducibility_score", "feasibility_score"],
        }
        for target in targets:
            for field in target_score_fields.get(target, []):
                score_updates[field] = max(6.0, float(getattr(review, field)))
        resulting_scores = [
            score_updates.get("evidence_quality_score", review.evidence_quality_score),
            score_updates.get("methodological_validity_score", review.methodological_validity_score),
            score_updates.get("feasibility_score", review.feasibility_score),
            score_updates.get("reproducibility_score", review.reproducibility_score),
            score_updates.get("claim_support_score", review.claim_support_score),
            score_updates.get("uncertainty_handling_score", review.uncertainty_handling_score),
        ]
        if min(resulting_scores) < 6:
            return review

        deferred_suggestions = list(
            dict.fromkeys(review.non_blocking_issues + review.blocking_issues)
        )
        return review.model_copy(
            update={
                **score_updates,
                "blocking_issues": [],
                "non_blocking_issues": deferred_suggestions,
                "decision": "approve",
                "failed_quality_gates": [],
                "required_revision_target": "none",
                "revision_plan": [],
                "approval_conditions": list(
                    dict.fromkeys(
                        review.approval_conditions
                        + [
                            "Human-approved blocking issues passed criterion-level verification.",
                            "New non-integrity suggestions are retained as non-blocking follow-up items.",
                            "Planning-only limitations and execution prerequisites remain explicitly disclosed.",
                        ]
                    )
                ),
            }
        )

    def get_revision_review(self, project_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        if project.phase != ResearchPhase.HUMAN_REVISION_REVIEW:
            raise InvalidTransitionError("Revision issues are reviewable only in HUMAN_REVISION_REVIEW.")
        review = project.reviews[-1] if project.reviews else None
        return {
            "project_id": project.project_id,
            "phase": project.phase.value,
            "review_version": len(project.reviews),
            "revision_cycle": project.iteration + 1,
            "review": review.model_dump(mode="json") if review else None,
            "issues": [issue.model_dump(mode="json") for issue in project.revision_issues],
            "latest_plan": (
                project.approved_revision_plans[-1].model_dump(mode="json")
                if project.approved_revision_plans
                else None
            ),
            "recovery_messages": project.revision_recovery_messages,
        }

    def submit_revision_review(
        self,
        project_id: str,
        decisions: list[RevisionIssueDecision],
    ) -> ResearchProject:
        project = self.get_project(project_id)
        if project.phase != ResearchPhase.HUMAN_REVISION_REVIEW:
            raise InvalidTransitionError("Revision plan can only be submitted from HUMAN_REVISION_REVIEW.")
        if project.iteration >= project.max_iterations:
            raise InvalidTransitionError("项目已达到最大修订轮次，不能开始新的自动修订。")
        plan = build_approved_revision_plan(project, decisions)
        project.approved_revision_plans.append(plan)
        project.active_revision_plan_id = plan.revision_plan_id
        project.current_revision_action = None
        project.pending_revision_actions = []
        project.iteration += 1
        project.budget.used_iterations += 1
        project.revision_feedback.extend(plan.human_modified_instructions.values())
        project.human_revision_history.append(
            HumanRevisionRecord(
                project_id=project.project_id,
                target="revision_plan",
                feedback=(
                    f"Approved {len(plan.approved_issues)} issues; deferred {len(plan.deferred_issues)}; "
                    f"accepted as limitations {len(plan.accepted_as_limitation)}; rejected {len(plan.rejected_issues)}."
                ),
                artifact_versions=dict(project.active_artifact_versions),
            )
        )
        self._invalidate_approval(project, f"Human approved revision cycle {plan.revision_cycle}.")
        project.phase = ResearchPhase.REVISION if plan.target_batches else ResearchPhase.CRITICAL_REVIEW
        event = completed_event(
            project.project_id,
            ResearchPhase.HUMAN_REVISION_REVIEW,
            "human_reviewer",
            visibility="user",
            display_key=f"revision_cycle_{plan.revision_cycle}_approved",
            display_markdown=(
                f"您已确认第 {plan.revision_cycle} 轮修订计划：{len(plan.target_batches)} 个产物批次等待执行。"
            ),
        )
        self._append_event(project, event)
        self.store.save(project)
        return project

    def defer_revision_review(self, project_id: str, reason: str = "") -> ResearchProject:
        project = self.get_project(project_id)
        if project.phase != ResearchPhase.HUMAN_REVISION_REVIEW:
            raise InvalidTransitionError("Revision review can only be deferred from HUMAN_REVISION_REVIEW.")
        project.phase = ResearchPhase.HUMAN_INTERVENTION_REQUIRED
        project.stage_messages.append(reason or "用户暂缓处理独立审查提出的修订建议。")
        self.store.save(project)
        return project

    def resume_evidence_research(self, project_id: str) -> ResearchProject:
        """Recover an evidence revision that cannot be completed by the revision agent."""
        project = self.get_project(project_id)
        if project.phase != ResearchPhase.HUMAN_REVISION_REVIEW:
            raise InvalidTransitionError(
                "Evidence research can only be resumed from HUMAN_REVISION_REVIEW."
            )
        active_plan = next(
            (
                item
                for item in project.approved_revision_plans
                if item.revision_plan_id == project.active_revision_plan_id
            ),
            None,
        )
        recoverable = bool(
            active_plan
            and any(
                batch.target == "evidence" and batch.status == "needs_attention"
                for batch in active_plan.target_batches
            )
        )
        if not recoverable:
            raise InvalidTransitionError("No failed evidence revision is available to resume.")

        project.current_revision_action = None
        project.active_revision_plan_id = None
        message = (
            "证据不能由自动修订器生成。请重新执行有界检索与来源人工筛选；"
            "已上传并解析的项目资料会作为带来源标识的上下文保留。"
        )
        project.revision_recovery_messages.append(message)
        self._targeted_rollback(project, "evidence", message, ["evidence"])
        self.store.save(project)
        return project

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

    def _execute_approved_revision_plan(self, project: ResearchProject) -> tuple[list[str], str]:
        plan = next(
            (item for item in project.approved_revision_plans if item.revision_plan_id == project.active_revision_plan_id),
            None,
        )
        if plan is None:
            raise AIScientistError("Approved revision plan is missing.")
        if project.active_job_id is None:
            raise AIScientistError("Revision execution requires an active persisted job.")

        produced: list[str] = []
        plan.status = "in_progress"
        cycle_event = completed_event(
            project.project_id,
            ResearchPhase.REVISION,
            "orchestrator",
            job_id=project.active_job_id,
            status="revision_cycle_started",
            visibility="user",
            display_key=f"revision_cycle_{plan.revision_cycle}_started",
            display_markdown=f"第 {plan.revision_cycle} 轮修订开始，共 {len(plan.target_batches)} 个产物批次。",
        )
        self._append_event(project, cycle_event)

        issue_by_id = {item.issue_id: item for item in project.revision_issues}
        for index, batch in enumerate(plan.target_batches, start=1):
            if batch.status == "completed":
                continue
            issues = [issue.model_copy(deep=True) for issue in batch.issue_snapshots]
            if not issues:
                issues = [issue_by_id[issue_id] for issue_id in batch.issue_ids if issue_id in issue_by_id]
            if not issues:
                issues = [issue for issue in project.revision_issues if issue.target == batch.target]
            if not batch.issue_snapshots and issues:
                batch.issue_snapshots = [issue.model_copy(deep=True) for issue in issues]
            if not batch.completion_criteria and issues:
                batch.completion_criteria = list(
                    dict.fromkeys(
                        criterion for issue in issues for criterion in issue.completion_criteria
                    )
                )
            batch.status = "in_progress"
            batch.job_id = project.active_job_id
            batch.old_artifact_version = project.active_artifact_versions.get(batch.target)
            project.current_revision_action = RevisionAction(
                target=self._legacy_target(batch.target),
                priority=min((issue.priority for issue in issues), default=1),
                reason=f"Human-approved {batch.target} revision batch.",
                required_changes=list(batch.instructions),
                completion_criteria=list(batch.completion_criteria),
                action_id=batch.batch_id,
                status="in_progress",
            )
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    ResearchPhase.REVISION,
                    "orchestrator",
                    job_id=project.active_job_id,
                    status="revision_batch_started",
                    visibility="user",
                    display_key=f"revision_cycle_{plan.revision_cycle}_{batch.batch_id}_started",
                    display_markdown=f"{index} / {len(plan.target_batches)}　正在修订{self._revision_target_label(batch.target)}。",
                ),
            )

            if batch.target == "execution_requirements":
                project.execution_requirements.extend(batch.instructions)
                record = self.artifacts.save_json(
                    project.project_id,
                    "execution_requirements",
                    {"requirements": list(dict.fromkeys(project.execution_requirements))},
                    "human_reviewer",
                )
                project.artifacts.append(record)
                project.active_artifact_versions["execution_requirements"] = record.version
                produced.append(record.artifact_id)
                verification = RevisionVerificationResult(
                    action_id=batch.batch_id,
                    target_artifact=batch.target,
                    artifact_version=record.version,
                    criteria_results=[
                        RevisionCriterionResult(
                            criterion="Execution prerequisite is recorded without claiming execution.",
                            passed=True,
                            evidence="Recorded under execution_requirements.",
                        )
                    ],
                    overall_passed=True,
                    verification_method="deterministic_recording",
                )
                verification_artifact_id = self._record_revision_verification(project, plan, batch, verification)
                produced.append(verification_artifact_id)
            elif batch.target == "evidence":
                batch.status = "needs_attention"
                batch.job_id = None
                plan.status = "needs_attention"
                project.current_revision_action = None
                project.active_revision_plan_id = None
                reason = (
                    "证据不能由自动修订器生成。请重新执行有界检索与来源人工筛选；"
                    "已上传并解析的项目资料会作为带来源标识的上下文保留。"
                )
                project.revision_recovery_messages.append(reason)
                project.stage_messages.append(reason)
                self._targeted_rollback(project, "evidence", reason, ["evidence"])
                self.store.save(project)
                return produced, "revision_evidence_research_required"
            else:
                old_artifact = self._current_revision_artifact(project, batch.target)
                working_artifact = old_artifact
                previous_verification: RevisionVerificationResult | None = None
                for attempt in range(1, self.revision_max_attempts + 1):
                    run = self._run_revision_model(
                        project,
                        batch,
                        working_artifact,
                        issues,
                        previous_verification,
                    )
                    artifact_id, new_version = self._record_revision_output(project, plan, batch, run)
                    produced.append(artifact_id)
                    batch.new_artifact_version = new_version
                    new_artifact = self._current_revision_artifact(project, batch.target)
                    retry_remaining = attempt < self.revision_max_attempts
                    verification, verification_artifact_id = self._verify_revision_batch(
                        project,
                        plan,
                        batch,
                        issues,
                        working_artifact,
                        new_artifact,
                        new_version,
                        retry_remaining,
                    )
                    produced.append(verification_artifact_id)
                    if verification.overall_passed:
                        break
                    previous_verification = verification
                    working_artifact = new_artifact
                    if retry_remaining:
                        failed_count = len([item for item in verification.criteria_results if not item.passed])
                        self._append_event(
                            project,
                            completed_event(
                                project.project_id,
                                ResearchPhase.REVISION,
                                "orchestrator",
                                job_id=project.active_job_id,
                                status="revision_auto_repair_started",
                                visibility="user",
                                display_key=(
                                    f"revision_cycle_{plan.revision_cycle}_{batch.batch_id}_"
                                    f"auto_repair_after_v{new_version}"
                                ),
                                display_markdown=(
                                    f"验证发现 {failed_count} 项尚未满足，正在根据逐条反馈自动补修"
                                    f"{self._revision_target_label(batch.target)}。"
                                ),
                            ),
                        )
                if not verification.overall_passed:
                    batch.status = "needs_attention"
                    batch.job_id = None
                    plan.status = "needs_attention"
                    project.current_revision_action = project.current_revision_action.model_copy(
                        update={"status": "needs_attention"}
                    )
                    for issue in issues:
                        issue.status = "needs_attention"
                    project.phase = ResearchPhase.HUMAN_REVISION_REVIEW
                    project.stage_messages.append("AI 未能完整解决当前修订批次，已返回人工审查。")
                    self._append_event(
                        project,
                        completed_event(
                            project.project_id,
                            ResearchPhase.REVISION,
                            "revision_verifier",
                            job_id=project.active_job_id,
                            status="revision_verification_failed",
                            visibility="user",
                            display_key=f"revision_cycle_{plan.revision_cycle}_{batch.batch_id}_needs_attention_v{new_version}",
                            display_markdown="修订验证未通过。AI 未完整满足部分要求，请补充指令、提供内容或调整处置方式。",
                        ),
                    )
                    self.store.save(project)
                    return produced, "revision_needs_attention"

            batch.status = "completed"
            batch.job_id = None
            for issue in issues:
                issue.status = "completed"
            completed_action = project.current_revision_action.model_copy(update={"status": "completed"})
            project.completed_revision_actions.append(completed_action)
            project.current_revision_action = None
            passed_count = len([item for item in verification.criteria_results if item.passed])
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    ResearchPhase.REVISION,
                    "revision_verifier",
                    job_id=project.active_job_id,
                    status="revision_batch_completed",
                    visibility="user",
                    display_key=f"revision_cycle_{plan.revision_cycle}_{batch.batch_id}_completed_v{verification.artifact_version}",
                    display_markdown=(
                        f"{self._revision_target_label(batch.target)} v{verification.artifact_version} 修订完成；"
                        f"验证 {passed_count}/{len(verification.criteria_results)} 条通过。"
                    ),
                ),
            )

        plan.status = "completed"
        project.current_revision_action = None
        project.active_revision_plan_id = None
        project.phase = ResearchPhase.CRITICAL_REVIEW
        self._append_event(
            project,
            completed_event(
                project.project_id,
                ResearchPhase.REVISION,
                "orchestrator",
                job_id=project.active_job_id,
                status="revision_cycle_completed",
                visibility="user",
                display_key=f"revision_cycle_{plan.revision_cycle}_completed",
                display_markdown=f"第 {plan.revision_cycle} 轮全部修订批次已验证完成，下一步将进行独立复审。",
            ),
        )
        return produced, "revision_cycle_completed"

    def _run_revision_model(
        self,
        project: ResearchProject,
        batch: RevisionTargetBatch,
        old_artifact: Any,
        issues: list[RevisionIssue],
        previous_verification: RevisionVerificationResult | None = None,
    ) -> AgentRun:
        role_and_model = {
            "question": ("research_director", ResearchDirectorAgent.output_model),
            "hypothesis": ("hypothesis_scientist", HypothesisScientistAgent.output_model),
            "methodology": ("methodologist", MethodologistAgent.output_model),
            "study_design": ("study_designer", StudyDesignerAgent.output_model),
            "analysis_plan": ("analyst", AnalystAgent.output_model),
            "reproducibility_plan": ("reproducibility_engineer", ReproducibilityEngineerAgent.output_model),
        }
        if batch.target not in role_and_model:
            raise AIScientistError(f"Unsupported automated revision target: {batch.target}")
        role, output_model = role_and_model[batch.target]
        result = self._structured_client_for_project(project).call(
            role,
            (
                "Revise one existing research-planning artifact using only the approved human revision batch. "
                "Implement every completion criterion as concrete text or fields in the artifact; general promises "
                "are not completion. Preserve correct existing content. If prior verification feedback is supplied, "
                "repair every failed criterion and retain criteria that already passed. "
                "Do not claim that future execution tasks have already occurred. Return structured JSON only."
            ),
            {
                "project_id": project.project_id,
                "planning_only": project.planning_only,
                "research_question": project.question.model_dump(mode="json") if project.question else None,
                "current_artifact": old_artifact,
                "approved_revision_batch": batch.model_dump(mode="json"),
                "approved_issues": [issue.model_dump(mode="json") for issue in issues],
                "completion_criteria": list(
                    batch.completion_criteria
                    or dict.fromkeys(criterion for issue in issues for criterion in issue.completion_criteria)
                ),
                "human_instructions": batch.instructions,
                "human_provided_content": batch.provided_content,
                "previous_verification_failures": [
                    item.model_dump(mode="json")
                    for item in (previous_verification.criteria_results if previous_verification else [])
                    if not item.passed
                ],
            },
            output_model,
        )
        return AgentRun(output=result.value, metadata=result.metadata)

    def _record_revision_output(
        self,
        project: ResearchProject,
        plan: ApprovedRevisionPlan,
        batch: RevisionTargetBatch,
        run: AgentRun,
    ) -> tuple[str, int]:
        self._apply_call_metadata(project, run.metadata)
        artifact_type = batch.target
        self._apply_revision_output(project, artifact_type, run.output)
        record = self.artifacts.save_json(
            project.project_id,
            artifact_type,
            run.output.model_dump(mode="json"),
            run.metadata.agent_name,
        )
        project.artifacts.append(record)
        project.active_artifact_versions[artifact_type] = record.version
        self._invalidate_approval(project, f"Revision cycle {plan.revision_cycle} produced {artifact_type} v{record.version}.")
        self._append_event(
            project,
            completed_event(
                project.project_id,
                ResearchPhase.REVISION,
                run.metadata.agent_name,
                job_id=project.active_job_id,
                requested_model=run.metadata.requested_model,
                actual_model=run.metadata.actual_model,
                fallback_used=run.metadata.fallback_used,
                output_artifact_ids=[record.artifact_id],
                attempted_calls=run.metadata.attempted_calls,
                successful_calls=run.metadata.successful_calls,
                visibility="user",
                display_key=f"revision_cycle_{plan.revision_cycle}_{batch.batch_id}_{artifact_type}_v{record.version}_generated",
                display_markdown=f"已生成 {self._revision_target_label(artifact_type)} v{record.version}，正在验证修订要求。",
            ),
        )
        return record.artifact_id, record.version

    def _verify_revision_batch(
        self,
        project: ResearchProject,
        plan: ApprovedRevisionPlan,
        batch: RevisionTargetBatch,
        issues: list[RevisionIssue],
        old_artifact: Any,
        new_artifact: Any,
        new_version: int,
        retry_remaining: bool = False,
    ) -> tuple[RevisionVerificationResult, str]:
        deterministic = deterministic_verify_batch(batch, issues, new_artifact)
        try:
            verifier = RevisionVerifierAgent(client=self._structured_client_for_project(project))
            run = verifier.verify(
                project, batch, issues, old_artifact, new_artifact, new_version, deterministic
            )
            self._apply_call_metadata(project, run.metadata)
            model_result = run.output
            combined = combine_revision_verification_results(
                deterministic,
                model_result.criteria_results,
            )
            verification = model_result.model_copy(
                update={
                    "action_id": batch.batch_id,
                    "target_artifact": batch.target,
                    "artifact_version": new_version,
                    "criteria_results": combined,
                    "overall_passed": bool(combined) and all(item.passed for item in combined),
                    "verification_method": "deterministic+independent_qwen",
                }
            )
        except Exception as exc:
            verification = RevisionVerificationResult(
                action_id=batch.batch_id,
                target_artifact=batch.target,
                artifact_version=new_version,
                criteria_results=[
                    item.model_copy(
                        update={
                            "passed": False,
                            "note": f"{item.note} Independent verification failed: {type(exc).__name__}",
                        }
                    )
                    for item in deterministic
                ],
                overall_passed=False,
                verification_method="deterministic+independent_verifier_failed",
            )
        artifact_id = self._record_revision_verification(
            project,
            plan,
            batch,
            verification,
            retry_remaining=retry_remaining,
        )
        return verification, artifact_id

    def _record_revision_verification(
        self,
        project: ResearchProject,
        plan: ApprovedRevisionPlan,
        batch: RevisionTargetBatch,
        verification: RevisionVerificationResult,
        retry_remaining: bool = False,
    ) -> str:
        project.revision_verifications.append(verification)
        batch.verification_id = verification.verification_id
        record = self.artifacts.save_json(
            project.project_id,
            "revision_verification",
            verification.model_dump(mode="json"),
            "revision_verifier",
        )
        project.artifacts.append(record)
        self._append_event(
            project,
            completed_event(
                project.project_id,
                ResearchPhase.REVISION,
                "revision_verifier",
                job_id=project.active_job_id,
                output_artifact_ids=[record.artifact_id],
                visibility="user",
                display_key=f"revision_cycle_{plan.revision_cycle}_{batch.batch_id}_verification_v{verification.artifact_version}",
                display_markdown=(
                    "修订验证通过。"
                    if verification.overall_passed
                    else (
                        "修订验证发现可补全项，准备自动定向补修。"
                        if retry_remaining
                        else "修订验证未通过，需要人工处理。"
                    )
                ),
            ),
        )
        return record.artifact_id

    @staticmethod
    def _apply_revision_output(project: ResearchProject, target: str, output: Any) -> None:
        if target == "question":
            project.question = output.research_question
            project.title = output.project_title
        elif target == "hypothesis":
            project.hypotheses = output.hypotheses
        elif target == "methodology":
            project.research_mode = output.selected_research_mode
            project.method_rationale = output.methodological_rationale
            project.validity_threats = output.validity_threats
            project.required_controls = output.required_controls
        elif target == "study_design":
            project.study_design = output
        elif target == "analysis_plan":
            project.analysis_plan = output
        elif target == "reproducibility_plan":
            project.reproducibility_plan = output.model_dump(mode="json")

    def _current_revision_artifact(self, project: ResearchProject, target: str) -> Any:
        return {
            "question": project.question.model_dump(mode="json") if project.question else None,
            "hypothesis": [item.model_dump(mode="json") for item in project.hypotheses],
            "methodology": {
                "selected_research_mode": project.research_mode.value if project.research_mode else None,
                "methodological_rationale": project.method_rationale,
                "validity_threats": project.validity_threats,
                "required_controls": project.required_controls,
            },
            "study_design": project.study_design.model_dump(mode="json") if project.study_design else None,
            "analysis_plan": project.analysis_plan.model_dump(mode="json") if project.analysis_plan else None,
            "reproducibility_plan": project.reproducibility_plan,
        }.get(target)

    @staticmethod
    def _legacy_target(target: str) -> str:
        return {
            "methodology": "method",
            "execution_requirements": "reproducibility_plan",
        }.get(target, target)

    @staticmethod
    def _revision_target_label(target: str) -> str:
        return {
            "question": "研究问题",
            "evidence": "证据",
            "hypothesis": "研究假设",
            "methodology": "方法学方案",
            "study_design": "研究设计",
            "analysis_plan": "分析方案",
            "reproducibility_plan": "可复现性方案",
            "execution_requirements": "执行阶段要求",
        }.get(target, target)

    def debug_claim_mapping(self, project_id: str) -> dict[str, Any]:
        """Dry-run claim mapping validation without changing formal project state."""

        project = self.get_project(project_id).model_copy(deep=True)
        try:
            collection = self._latest_evidence_collection(project)
            if not collection.evidence_items:
                return {
                    "status": "insufficient_evidence",
                    "message": "当前没有可进入主张分析的有效证据。",
                    "debug": {
                        "evidence_count": 0,
                        "claim_count": 0,
                        "invalid_reference_count": 0,
                        "schema_valid": True,
                        "model_called": False,
                    },
                }
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
            raise AIScientistError(
                "Evidence Researcher does not implement the required human-curation interface."
            )
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
        if checkpoint.search_plan is not None:
            validate_checkpoint_binding(project)
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
            plan = bind_search_plan(project, plan_run.output, plan_run.metadata.actual_model)
            deterministic_status, deterministic_note = deterministic_plan_relevance(project, plan)
            semantic_note = ""
            if deterministic_status != "irrelevant" and hasattr(agent, "validate_search_plan_semantics"):
                semantic_run = agent.validate_search_plan_semantics(project, plan)
                self._apply_call_metadata(project, semantic_run.metadata)
                semantic_status = semantic_run.output.status
                semantic_note = semantic_run.output.reason
                status_order = {"relevant": 0, "partially_relevant": 1, "irrelevant": 2}
                relevance_status = max(
                    [deterministic_status, semantic_status], key=lambda value: status_order[value]
                )
            else:
                relevance_status = deterministic_status
            plan = plan.model_copy(
                update={
                    "relevance_status": relevance_status,
                    "relevance_note": " ".join(item for item in [deterministic_note, semantic_note] if item),
                }
            )
            checkpoint.search_plan = plan
            checkpoint.project_id = project.project_id
            checkpoint.question_hash = plan.question_hash
            checkpoint.search_plan_id = plan.search_plan_id
            checkpoint.awaiting_search_plan_review = True
            checkpoint.search_plan_approved = False
            project.search_plan_history.append(plan)
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    phase,
                    "evidence_researcher",
                    status="search_plan_completed",
                    stage_substep="search_planning",
                    query_count=len(plan.queries),
                    requested_model=plan_run.metadata.requested_model,
                    actual_model=plan_run.metadata.actual_model,
                    fallback_used=plan_run.metadata.fallback_used,
                ),
            )
            produced += self._save_search_checkpoint(project, checkpoint)
            if project.evidence_review_mode != "AUTO" and not project.auto_approve_search_plan:
                project.phase = ResearchPhase.SEARCH_PLAN_REVIEW
                self._append_event(
                    project,
                    completed_event(
                        project.project_id,
                        phase,
                        "evidence_researcher",
                        status="search_plan_review_required",
                        visibility="user",
                        display_key=f"search_plan_review_v{plan.version}",
                        display_markdown=(
                            "AI 已生成检索方案，请核对检索式是否针对当前研究问题。"
                            if relevance_status != "irrelevant"
                            else "系统生成的检索方案可能偏离研究问题，请重新生成或人工修改。"
                        ),
                    ),
                )
                return produced
            if relevance_status == "irrelevant":
                project.phase = ResearchPhase.SEARCH_PLAN_REVIEW
                return produced
            checkpoint.search_plan = plan.model_copy(update={"approved_at": utc_now(), "approved_by": "system"})
            checkpoint.awaiting_search_plan_review = False
            checkpoint.search_plan_approved = True

        validate_search_plan_binding(project, checkpoint.search_plan)
        if not checkpoint.search_plan_approved:
            project.phase = ResearchPhase.SEARCH_PLAN_REVIEW
            return produced

        records_by_query = {item.query: item for item in checkpoint.query_records}
        last_search_exception: Exception | None = None
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
                last_search_exception = exc
                original_error = self._sanitize_text(str(exc))
                if self._is_timeout_error(exc) and fallback_search_model and fallback_search_model != requested_search_model:
                    actual_model = fallback_search_model
                    fallback_reason = "Primary search query timed out."
                    try:
                        result = agent.search_one_query(query, fallback_search_model)
                        status = "completed"
                    except Exception as fallback_exc:
                        last_search_exception = fallback_exc
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
            if last_search_exception is not None:
                raise last_search_exception
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

        if not checkpoint.awaiting_source_review and not checkpoint.source_selection_completed:
            deduplicated = select_sources(checkpoint.candidates, maximum=max(1, len(checkpoint.candidates)))
            checkpoint.candidates = enrich_candidates(project, deduplicated)
            collection = SourceCandidateCollection(
                project_id=project.project_id,
                question_hash=checkpoint.question_hash,
                search_plan_id=checkpoint.search_plan_id,
                candidates=checkpoint.candidates,
            )
            project.source_candidate_collections.append(collection)
            candidate_record = self.artifacts.save_json(
                project.project_id,
                "source_candidate_collection",
                collection.model_dump(mode="json"),
                "evidence_researcher",
            )
            project.artifacts.append(candidate_record)
            produced.append(candidate_record.artifact_id)
            if project.evidence_review_mode == "AUTO":
                recommended = [item for item in checkpoint.candidates if item.ai_recommendation == "keep"][:max_sources]
                if recommended:
                    kept_ids = [item.candidate_id for item in recommended]
                    auto_decisions = [
                        CuratedSource(
                            candidate_id=item.candidate_id,
                            decision="keep" if item.candidate_id in kept_ids else "reject",
                            decided_by="system",
                            rejection_reason="AI recommendation" if item.candidate_id not in kept_ids else "",
                        )
                        for item in checkpoint.candidates
                    ]
                    snapshot = SourceSelectionSnapshot(
                        project_id=project.project_id,
                        iteration=project.iteration,
                        research_question_version=research_question_version(project),
                        search_plan_version=checkpoint.search_plan.version,
                        kept_candidate_ids=kept_ids,
                        rejected_candidate_ids=[
                            item.candidate_id for item in checkpoint.candidates if item.candidate_id not in kept_ids
                        ],
                        decisions=auto_decisions,
                        created_by="system",
                        selection_note="AUTO evidence review mode",
                    )
                    project.curated_sources.extend(auto_decisions)
                    project.source_selection_snapshots.append(snapshot)
                    checkpoint.selected_candidates = recommended
                    checkpoint.source_selection_id = snapshot.selection_id
                    checkpoint.source_selection_completed = True
                else:
                    checkpoint.awaiting_source_review = True
                    project.phase = ResearchPhase.HUMAN_SOURCE_REVIEW
            else:
                checkpoint.awaiting_source_review = True
                project.phase = ResearchPhase.HUMAN_SOURCE_REVIEW
                produced += self._save_search_checkpoint(project, checkpoint)
                self._append_event(
                    project,
                    completed_event(
                        project.project_id,
                        phase,
                        "evidence_researcher",
                        status="human_source_review_required",
                        stage_substep="candidate_source_enrichment",
                        search_result_count=len(checkpoint.candidates),
                        visibility="user",
                        display_key=f"human_source_review_{collection.collection_id}",
                        summary_markdown=(
                            f"发现{len(checkpoint.candidates)}个候选来源。AI 建议保留"
                            f"{len([item for item in checkpoint.candidates if item.ai_recommendation == 'keep'])}个，"
                            "等待人工确认。"
                        ),
                    ),
                )
                return produced

        if checkpoint.awaiting_source_review or not checkpoint.source_selection_completed:
            project.phase = ResearchPhase.HUMAN_SOURCE_REVIEW
            return produced

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
        latest_selection = project.source_selection_snapshots[-1] if project.source_selection_snapshots else None
        if latest_selection:
            candidates_by_url = {item.url: item for item in checkpoint.selected_candidates if item.url}
            candidates_by_doi = {item.doi: item for item in checkpoint.selected_candidates if item.doi}
            candidates_by_pmid = {item.pmid: item for item in checkpoint.selected_candidates if item.pmid}
            project.evidence = [
                item.model_copy(
                    update={
                        "selection_provenance": SelectionProvenance(
                            selected_by=latest_selection.created_by,
                            selection_id=latest_selection.selection_id,
                            candidate_id=(
                                candidates_by_url.get(item.source_url)
                                or candidates_by_doi.get(item.doi)
                                or candidates_by_pmid.get(item.pmid)
                            ).candidate_id,
                            verification_method=item.verification_method,
                        )
                    }
                )
                if (
                    candidates_by_url.get(item.source_url)
                    or candidates_by_doi.get(item.doi)
                    or candidates_by_pmid.get(item.pmid)
                )
                else item
                for item in project.evidence
            ]
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
        minimum_verified = max(1, int(os.getenv("AI_SCIENTIST_MIN_VERIFIED_EVIDENCE", "1")))
        usable_evidence = [
            item for item in project.evidence
            if item.verification_status in {"verified", "partially_verified"} and not item.duplicate_of
        ]
        if len(usable_evidence) < minimum_verified:
            checkpoint.awaiting_source_review = True
            checkpoint.source_selection_completed = False
            project.phase = ResearchPhase.HUMAN_SOURCE_REVIEW
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    phase,
                    "evidence_researcher",
                    status="insufficient_verified_evidence",
                    visibility="user",
                    display_key=f"zero_evidence_gate_{len(project.source_selection_snapshots)}",
                    display_markdown="当前选定资料未形成足够的可验证证据。请调整资料选择、补充来源或重新检索。",
                    evidence_count=len(usable_evidence),
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
        minimum_verified = max(1, int(os.getenv("AI_SCIENTIST_MIN_VERIFIED_EVIDENCE", "1")))
        usable_evidence = [
            item for item in project.evidence
            if item.verification_status in {"verified", "partially_verified"} and not item.duplicate_of
        ]
        if len(usable_evidence) < minimum_verified:
            checkpoint.awaiting_source_review = True
            checkpoint.source_selection_completed = False
            project.phase = ResearchPhase.HUMAN_SOURCE_REVIEW
            self._append_event(
                project,
                completed_event(
                    project.project_id,
                    phase,
                    "evidence_researcher",
                    status="insufficient_verified_evidence",
                    visibility="user",
                    display_key=f"zero_evidence_gate_{len(project.source_selection_snapshots)}",
                    display_markdown=(
                        "当前选定资料未形成足够的可验证证据。请调整资料选择、补充来源或重新检索。"
                    ),
                    evidence_count=len(usable_evidence),
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
            if not evidence_collection.evidence_items:
                project.phase = ResearchPhase.HUMAN_SOURCE_REVIEW
                project.background_research_checkpoint.awaiting_source_review = True
                project.background_research_checkpoint.source_selection_completed = False
                self._append_event(
                    project,
                    completed_event(
                        project.project_id,
                        phase,
                        "orchestrator",
                        status="insufficient_evidence_for_claim_mapping",
                        visibility="user",
                        display_key=f"claim_mapping_evidence_gate_{project.iteration}",
                        display_markdown="当前没有可进入主张分析的有效证据，请重新选择资料、补充来源或重新检索。",
                        evidence_count=0,
                    ),
                )
                self.store.save(project)
                return produced

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
        project.active_artifact_versions[artifact_type] = record.version
        if artifact_type in project.stale_artifacts:
            project.stale_artifacts.remove(artifact_type)
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
            input_artifact_ids=list(dict.fromkeys(run.auxiliary.get("parsed_artifact_ids") or [])),
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
            visibility="user" if display_markdown else "internal",
            display_key=f"{artifact_type}_completed" if display_markdown else None,
        )
        used_asset_ids = set(run.auxiliary.get("parsed_asset_ids") or [])
        for asset in project.research_assets:
            if asset.asset_id in used_asset_ids and run.metadata.agent_name not in asset.used_by_agents:
                asset.used_by_agents.append(run.metadata.agent_name)
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
        project.active_artifact_versions[artifact_type] = record.version
        if artifact_type in project.stale_artifacts:
            project.stale_artifacts.remove(artifact_type)
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
            visibility="user" if display_markdown else "internal",
            display_key=f"{artifact_type}_completed" if display_markdown else None,
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
        project.active_artifact_versions[artifact_type] = record.version
        self._invalidate_approval(project, f"人工修改了 {artifact_type} v{record.version}。")
        event = completed_event(
            project.project_id,
            project.phase,
            "human",
            status="human_edit",
            output_artifact_ids=[record.artifact_id],
            changed_fields=list(content) if isinstance(content, dict) else [artifact_type],
            reason=reason,
            feedback=reason,
            visibility="user",
            display_key=f"human_edit_{artifact_type}_{record.version}",
            display_markdown=f"人工已更新{artifact_type}，原因：{reason or '未填写'}。",
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
            "reproducibility": ResearchPhase.FEASIBILITY_REVIEW,
        }
        target_phase = target_map[target]
        if target in {"question", "evidence"}:
            project.background_research_checkpoint = BackgroundResearchCheckpoint()
            project.curated_sources = []
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
            visibility="user",
            display_key=f"human_revision_{target}_{len(project.human_revision_history)}",
            display_markdown=f"人工审查要求修订{target}。{reason or '请按审查意见更新。'}",
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
            visibility="user",
            display_key="final_research_plan_generated",
            display_markdown="科学综合与最终研究计划已生成。",
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
            ResearchPhase.SEARCH_PLAN_REVIEW: "human_reviewer",
            ResearchPhase.HUMAN_SOURCE_REVIEW: "human_reviewer",
            ResearchPhase.HYPOTHESIS_GENERATION: "hypothesis_scientist",
            ResearchPhase.METHOD_SELECTION: "methodologist",
            ResearchPhase.STUDY_DESIGN: "study_designer",
            ResearchPhase.ANALYSIS_PLANNING: "analyst",
            ResearchPhase.FEASIBILITY_REVIEW: "skeptical_reviewer",
            ResearchPhase.CRITICAL_REVIEW: "skeptical_reviewer",
            ResearchPhase.SYNTHESIS: "scientific_synthesizer",
        }.get(phase, "orchestrator")

    def _append_event(self, project: ResearchProject, event: ResearchEvent) -> None:
        event.iteration = project.iteration
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
        self.store.save(project)

    def _review_artifact_versions(self, project: ResearchProject) -> dict[str, int | None]:
        keys = [
            "research_question",
            "evidence_map",
            "claim_evidence_mapping",
            "hypotheses",
            "methodology",
            "study_design",
            "analysis_plan",
            "reproducibility_plan",
            "independent_review",
        ]
        return {
            key: project.active_artifact_versions.get(key, self._latest_artifact_version(project, key))
            for key in keys
        }

    def _ensure_review_package(self, project: ResearchProject) -> ReviewPackage:
        versions = self._review_artifact_versions(project)
        review = project.reviews[-1] if project.reviews else None
        if project.review_package and project.review_package.artifact_versions == versions:
            return project.review_package
        required = {
            "research_question", "evidence_map", "claim_evidence_mapping", "hypotheses",
            "study_design", "analysis_plan", "reproducibility_plan", "independent_review",
        }
        ready = review is not None and review.decision == "approve" and not review.blocking_issues
        ready = ready and all(versions.get(key) is not None for key in required)
        package = ReviewPackage(
            project_id=project.project_id,
            artifact_versions=versions,
            artifact_snapshots=self._review_artifact_snapshots(project),
            blocking_issue_count=len(review.blocking_issues) if review else 0,
            reviewer_decision=review.decision if review else "",
            ready_for_approval=ready,
        )
        project.review_package = package
        project.approval_status = "pending"
        return package

    @staticmethod
    def _review_artifact_snapshots(project: ResearchProject) -> dict[str, Any]:
        """Freeze display-safe scientific products without prompts or provider payloads."""

        return {
            "research_question": project.question.model_dump(mode="json") if project.question else None,
            "evidence": [item.model_dump(mode="json") for item in project.evidence],
            "claims": [item.model_dump(mode="json") for item in project.claims],
            "hypotheses": [item.model_dump(mode="json") for item in project.hypotheses],
            "method": {
                "research_mode": project.research_mode.value if project.research_mode else None,
                "rationale": project.method_rationale,
                "validity_threats": project.validity_threats,
                "required_controls": project.required_controls,
            },
            "study_design": project.study_design.model_dump(mode="json") if project.study_design else None,
            "analysis_plan": project.analysis_plan.model_dump(mode="json") if project.analysis_plan else None,
            "reproducibility_plan": project.reproducibility_plan,
            "independent_review": project.reviews[-1].model_dump(mode="json") if project.reviews else None,
        }

    @staticmethod
    def _invalidate_approval(project: ResearchProject, summary: str) -> None:
        if project.approval_status == "valid" and project.human_approval_history:
            project.human_approval_history[-1].status = "stale"
        if project.approval_status in {"valid", "pending", "deferred"}:
            project.approval_status = "stale"
        project.review_package = None
        project.approval_valid_for_versions = {}
        project.version_change_summaries.append(summary)

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
