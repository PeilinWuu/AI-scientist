"""Sequential orchestration for structured, auditable research planning."""

from __future__ import annotations

import os
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
from src.ai_scientist.project_store import ProjectStore
from src.ai_scientist.schemas import (
    ResearchEvent,
    ResearchPhase,
    ResearchProject,
    ReviewResult,
    new_id,
    utc_now,
)
from src.ai_scientist.state_machine import ResearchStateMachine, TERMINAL_PHASES
from src.ai_scientist.tools.execution_adapter import ExecutionAdapter
from src.ai_scientist.tools.registry import ToolRegistry


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
        constraints: dict[str, Any] | None = None,
        max_iterations: int = 2,
        planning_only: bool = True,
    ) -> ResearchProject:
        max_calls = int(os.getenv("AI_SCIENTIST_MAX_MODEL_CALLS", "50"))
        project = ResearchProject(
            title=objective.strip()[:100] or "Untitled research project",
            objective=objective.strip(),
            domain_hint=domain_hint,
            constraints=constraints or {},
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

    def run_next_step(self, project_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        previous_phase = project.phase
        if project.phase in TERMINAL_PHASES:
            raise InvalidTransitionError(f"Project in terminal phase {project.phase.value} cannot continue.")
        produced_artifacts: list[str] = []
        review_decision: str | None = None
        stage_status = "completed"
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
                selection = self.method_selector.select(
                    project.question,
                    project.objective,
                    available_data=[item.filename for item in project.artifacts],
                    available_tools=project.available_tools,
                    constraints=project.constraints,
                )
                project.research_mode = selection.primary_research_mode
                project.secondary_modes = selection.secondary_modes
                project.method_rationale = selection.rationale
                project.missing_capabilities = sorted(
                    set(project.missing_capabilities + selection.unavailable_capabilities)
                )
                project.human_actions_required = selection.human_actions_required
                produced_artifacts += self._record_structured_output(
                    project, previous_phase, "method_selector", selection, "research_mode_selection"
                )
                self.state_machine.transition(project, "next")
            elif project.phase == ResearchPhase.DOMAIN_SELECTION:
                if project.question is None:
                    raise AIScientistError("Domain selection requires a formulated question.")
                selection = self.domain_router.route(project.question, project.domain_hint)
                project.domain = selection.primary_domain
                project.secondary_domains = selection.secondary_domains
                project.human_actions_required.extend(selection.clarification_questions)
                produced_artifacts += self._record_structured_output(
                    project, previous_phase, "domain_router", selection, "domain_selection"
                )
                self.state_machine.transition(project, "next")
            elif project.phase == ResearchPhase.BACKGROUND_RESEARCH:
                run = self._run_agent(project, EvidenceResearcherAgent)
                project.evidence = run.output.evidence
                project.claims = run.output.claims
                project.evidence_gaps = run.output.evidence_gaps
                project.conflicting_evidence = run.output.conflicting_evidence
                if run.auxiliary.get("response_id"):
                    project.previous_response_ids["evidence_search"] = str(run.auxiliary["response_id"])
                produced_artifacts += self._record_agent_output(project, previous_phase, run, "evidence_map")
                self.state_machine.transition(project, "next")
            elif project.phase == ResearchPhase.CLAIM_EVIDENCE_MAPPING:
                graph = ClaimGraph(project.evidence, project.claims)
                errors = graph.validate()
                if errors:
                    raise AIScientistError("Claim-evidence validation failed: " + "; ".join(errors))
                produced_artifacts += self._record_structured_output(
                    project,
                    previous_phase,
                    "claim_graph",
                    {
                        "claim_count": len(project.claims),
                        "evidence_count": len(project.evidence),
                        "unsupported_claim_ids": [item.claim_id for item in graph.find_unsupported_claims()],
                    },
                    "claim_evidence_validation",
                )
                self.state_machine.transition(project, "next")
            elif project.phase == ResearchPhase.HYPOTHESIS_GENERATION:
                run = self._run_agent(project, HypothesisScientistAgent)
                project.hypotheses = run.output.hypotheses
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
                project.reviews.append(review.output)
                project.human_actions_required.extend(review.output.blocking_issues)
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
                produced_artifacts += self._record_agent_output(project, previous_phase, run, "research_plan_synthesis")
                self.state_machine.transition(project, "next")
            else:
                raise InvalidTransitionError(f"No stage handler for phase {project.phase.value}")
            self.store.save(project)
        except Exception as exc:
            project.phase = ResearchPhase.FAILED
            event = ResearchEvent(
                project_id=project.project_id,
                phase=previous_phase,
                agent_name="orchestrator",
                status="failed",
                error=self._sanitize_error(exc),
                schema_valid=False,
                finished_at=utc_now(),
            )
            self._append_event(project, event)
            self.store.save(project)
            raise
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
        self.store.save(project)
        return project

    def request_revision(self, project_id: str, target_phase: str, feedback: str) -> ResearchProject:
        project = self.get_project(project_id)
        if project.phase != ResearchPhase.HUMAN_APPROVAL:
            raise InvalidTransitionError("Revision can only be requested from HUMAN_APPROVAL.")
        if target_phase not in {"question", "evidence", "hypothesis", "method", "design"}:
            raise ValueError(f"Unsupported revision target: {target_phase}")
        project.revision_feedback.append(feedback)
        project.pending_revision_target = target_phase
        self._transition_event(project, "revise")
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
        return agent_class().run(project)

    def _record_agent_output(
        self,
        project: ResearchProject,
        phase: ResearchPhase,
        run: AgentRun,
        artifact_type: str,
    ) -> list[str]:
        if project.budget.used_model_calls + run.metadata.model_calls > project.budget.max_model_calls:
            raise BudgetExceededError("AI Scientist model-call budget was exceeded during this stage.")
        project.budget.used_model_calls += run.metadata.model_calls
        record = self.artifacts.save_json(
            project.project_id,
            artifact_type,
            run.output.model_dump(mode="json"),
            run.metadata.agent_name,
        )
        project.artifacts.append(record)
        event = completed_event(
            project.project_id,
            phase,
            run.metadata.agent_name,
            requested_model=run.metadata.requested_model,
            actual_model=run.metadata.actual_model,
            fallback_used=run.metadata.fallback_used,
            output_artifact_ids=[record.artifact_id],
            tool_names=run.tool_names,
            token_usage=run.metadata.token_usage,
            started_at=run.metadata.started_at,
        )
        self._append_event(project, event)
        self.store.save(project)
        return [record.artifact_id]

    def _record_structured_output(
        self,
        project: ResearchProject,
        phase: ResearchPhase,
        created_by: str,
        output: Any,
        artifact_type: str,
    ) -> list[str]:
        content = output.model_dump(mode="json") if hasattr(output, "model_dump") else output
        record = self.artifacts.save_json(project.project_id, artifact_type, content, created_by)
        project.artifacts.append(record)
        event = completed_event(
            project.project_id,
            phase,
            created_by,
            output_artifact_ids=[record.artifact_id],
        )
        self._append_event(project, event)
        return [record.artifact_id]

    def _transition_event(self, project: ResearchProject, outcome: str) -> None:
        previous = project.phase
        self.state_machine.transition(project, outcome)
        event = completed_event(
            project.project_id,
            previous,
            "orchestrator",
            status=f"transition:{outcome}:{project.phase.value}",
        )
        self._append_event(project, event)

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
