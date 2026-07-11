"""Sequential orchestration for structured, auditable research planning."""

from __future__ import annotations

import os
import traceback
from contextvars import ContextVar
from pathlib import Path
from typing import Any

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
from src.ai_scientist.domain_router import DomainRouter
from src.ai_scientist.events import completed_event
from src.ai_scientist.exceptions import AIScientistError, BudgetExceededError, InvalidTransitionError
from src.ai_scientist.method_selector import MethodSelector
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
    DomainSelectionOutput,
    EvidenceResearchOutput,
    EvidenceItem,
    Hypothesis,
    MethodologyOutput,
    ResearchEvent,
    ResearchPhase,
    ResearchQuestion,
    ResearchProject,
    ReviewResult,
    StudyDesign,
    new_id,
    utc_now,
)
from src.ai_scientist.state_machine import ResearchStateMachine, TERMINAL_PHASES
from src.ai_scientist.structured_client import StructuredQwenClient, StructuredCallMetadata
from src.ai_scientist.tools.execution_adapter import ExecutionAdapter
from src.ai_scientist.tools.registry import ToolRegistry


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
        max_iterations: int = 2,
        planning_only: bool = True,
    ) -> ResearchProject:
        max_calls = int(os.getenv("AI_SCIENTIST_MAX_MODEL_CALLS", "50"))
        project = ResearchProject(
            title=objective.strip()[:100] or "Untitled research project",
            objective=objective.strip(),
            domain_hint=domain_hint,
            constraints={**(constraints or {}), **({"constraints_text": constraints_text} if constraints_text else {})},
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
                project.domain = selection.primary_domain
                project.secondary_domains = selection.secondary_domains
                project.human_actions_required.extend(selection.clarification_questions)
                produced_artifacts += self._record_structured_output(
                    project,
                    previous_phase,
                    "research_director",
                    selection,
                    "domain_selection",
                    metadata=metadata,
                    display_markdown=render_domain_selection(selection.primary_domain, selection.secondary_domains),
                )
                self.state_machine.transition(project, "next")
            elif project.phase == ResearchPhase.BACKGROUND_RESEARCH:
                run = self._run_agent(project, EvidenceResearcherAgent)
                project.evidence = enrich_evidence_items(run.output.evidence)
                project.claims = run.output.claims
                project.evidence_gaps = run.output.evidence_gaps
                project.conflicting_evidence = run.output.conflicting_evidence
                self._refresh_quality_metrics(project)
                if run.auxiliary.get("response_id"):
                    project.previous_response_ids["evidence_search"] = str(run.auxiliary["response_id"])
                produced_artifacts += self._record_agent_output(project, previous_phase, run, "evidence_map")
                self.state_machine.transition(project, "next")
            elif project.phase == ResearchPhase.CLAIM_EVIDENCE_MAPPING:
                mapping, metadata = self._run_claim_mapping(project)
                project.evidence = enrich_evidence_items(mapping.evidence or project.evidence)
                project.claims = mapping.claims or project.claims
                project.evidence_gaps = mapping.evidence_gaps or project.evidence_gaps
                project.conflicting_evidence = mapping.conflicting_evidence or project.conflicting_evidence
                graph = ClaimGraph(project.evidence, project.claims)
                errors = graph.validate()
                if errors:
                    raise AIScientistError("Claim-evidence validation failed: " + "; ".join(errors))
                self._refresh_quality_metrics(project)
                produced_artifacts += self._record_structured_output(
                    project,
                    previous_phase,
                    "evidence_researcher",
                    {
                        "claim_count": len(project.claims),
                        "evidence_count": len(project.evidence),
                        "unsupported_claim_ids": [item.claim_id for item in graph.find_unsupported_claims()],
                    },
                    "claim_evidence_validation",
                    metadata=metadata,
                    display_markdown=render_claim_mapping(project),
                )
                self.state_machine.transition(project, "next")
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
                self.state_machine.transition(project, review_decision)
            elif project.phase == ResearchPhase.HUMAN_APPROVAL:
                stage_status = "awaiting_human_approval"
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
                self.state_machine.transition(project, review_decision)
            elif project.phase == ResearchPhase.SYNTHESIS:
                run = self._run_agent(project, ScientificSynthesizerAgent)
                project.conclusion = run.output
                self._refresh_quality_metrics(project)
                produced_artifacts += self._record_agent_output(project, previous_phase, run, "research_plan_synthesis")
                produced_artifacts += self._write_research_plan(project)
                self.state_machine.transition(project, "next")
            else:
                raise InvalidTransitionError(f"No stage handler for phase {project.phase.value}")
            self.store.save(project)
        except Exception as exc:
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
                failing_component=self._agent_name_for_phase(previous_phase),
                stage_substep=self._substep_for_error(previous_phase, exc),
                attempted_model=self._agent_name_for_phase(previous_phase),
                fallback_attempted=True,
                tool_name="web_search" if previous_phase == ResearchPhase.BACKGROUND_RESEARCH else None,
                safe_traceback=self._sanitize_traceback(exc),
                display_markdown=self._failure_display_message(previous_phase, exc),
                failed_calls=1,
            )
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

    def _run_agent(self, project: ResearchProject, agent_class: type) -> AgentRun:
        self._ensure_budget(project, 1)
        try:
            return agent_class().run(project)
        except Exception:
            project.budget.attempted_model_calls += 1
            project.budget.failed_model_calls += 1
            self.store.save(project)
            raise

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
            result = StructuredQwenClient().call(
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

    def _run_claim_mapping(self, project: ResearchProject) -> tuple[EvidenceResearchOutput, StructuredCallMetadata]:
        payload = {
            "question": project.question.model_dump(mode="json") if project.question else None,
            "evidence": [item.model_dump(mode="json") for item in project.evidence],
            "claims": [item.model_dump(mode="json") for item in project.claims],
            "evidence_gaps": project.evidence_gaps,
            "conflicting_evidence": project.conflicting_evidence,
            "task": (
                "Refine the claim-evidence mapping using only the provided evidence records. "
                "Do not invent new URLs or citations. Keep unsupported claims explicitly marked."
            ),
        }
        try:
            result = StructuredQwenClient().call(
                "evidence_researcher",
                "You are an AI Scientist evidence researcher. Return validated evidence and claim mapping only.",
                payload,
                EvidenceResearchOutput,
            )
            return result.value, result.metadata
        except Exception:
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
            ResearchPhase.BACKGROUND_RESEARCH: {"evidence_map", "claim_evidence_validation"},
            ResearchPhase.HYPOTHESIS_GENERATION: {"hypotheses"},
            ResearchPhase.METHOD_SELECTION: {"methodology"},
            ResearchPhase.STUDY_DESIGN: {"study_design"},
            ResearchPhase.ANALYSIS_PLANNING: {"analysis_plan", "reproducibility_plan", "independent_review", "research_plan"},
        }
        affected = phase_artifacts.get(target_phase, set())
        return [item.artifact_id for item in project.artifacts if item.artifact_type in affected]

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
        project.events.append(event.event_id)
        self.store.append_event(event)
        self.store.save(project)

    @staticmethod
    def _ensure_budget(project: ResearchProject, required_calls: int) -> None:
        if project.budget.used_model_calls + required_calls > project.budget.max_model_calls:
            raise BudgetExceededError("AI Scientist model-call budget is exhausted.")

    @staticmethod
    def _sanitize_error(exc: Exception) -> str:
        message = f"{type(exc).__name__}: {exc}"
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        return message.replace(api_key, "[REDACTED_API_KEY]") if api_key else message

    @classmethod
    def _sanitize_traceback(cls, exc: Exception) -> str:
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        return text.replace(api_key, "[REDACTED_API_KEY]") if api_key else text

    @staticmethod
    def _substep_for_error(phase: ResearchPhase, exc: Exception) -> str:
        if phase == ResearchPhase.BACKGROUND_RESEARCH:
            message = str(exc).lower()
            if "search" in message or "responses api" in message:
                return "search_call"
            if "validation" in message or "schema" in message:
                return "schema_validation"
            if "source" in message or "evidence" in message:
                return "evidence_normalization"
        return "model_call"

    @staticmethod
    def _failure_display_message(phase: ResearchPhase, exc: Exception) -> str:
        if phase == ResearchPhase.BACKGROUND_RESEARCH:
            return "证据研究阶段在整理搜索结果时失败。项目仍保留在上一完整阶段，可以重试。"
        return f"{phase.value} 阶段执行失败，项目已保留在上一完整阶段。"


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None
