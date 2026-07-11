"""Validated state transitions for one-stage-at-a-time research workflows."""

from __future__ import annotations

from pathlib import Path

import yaml

from src.ai_scientist.exceptions import InvalidTransitionError
from src.ai_scientist.schemas import ResearchPhase, ResearchProject


TERMINAL_PHASES = {ResearchPhase.COMPLETED, ResearchPhase.FAILED, ResearchPhase.CANCELLED}


class ResearchStateMachine:
    """Load and enforce transitions declared in the workflow YAML."""

    def __init__(self, workflow_path: str | Path | None = None) -> None:
        path = Path(workflow_path or Path(__file__).parent / "workflows" / "general_research_v1.yaml")
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.transitions: dict[str, dict[str, str]] = raw["transitions"]

    def next_phase(self, project: ResearchProject, outcome: str = "next") -> ResearchPhase:
        if project.phase in TERMINAL_PHASES:
            raise InvalidTransitionError(f"Project in terminal phase {project.phase.value} cannot continue.")
        phase_transitions = self.transitions.get(project.phase.value, {})
        target = phase_transitions.get(outcome)
        if not target:
            raise InvalidTransitionError(
                f"Outcome {outcome!r} is not valid from phase {project.phase.value}."
            )
        return ResearchPhase(target)

    def transition(self, project: ResearchProject, outcome: str = "next") -> ResearchPhase:
        target = self.next_phase(project, outcome)
        if target in {
            ResearchPhase.QUESTION_FORMULATION,
            ResearchPhase.BACKGROUND_RESEARCH,
            ResearchPhase.HYPOTHESIS_GENERATION,
            ResearchPhase.METHOD_SELECTION,
            ResearchPhase.STUDY_DESIGN,
            ResearchPhase.ANALYSIS_PLANNING,
        } and project.phase in {ResearchPhase.FEASIBILITY_REVIEW, ResearchPhase.REVISION}:
            project.iteration += 1
            project.budget.used_iterations += 1
            if project.iteration > project.max_iterations:
                project.phase = ResearchPhase.FAILED
                return project.phase
        project.phase = target
        return target
