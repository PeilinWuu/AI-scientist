"""Reproducibility readiness role."""

from src.ai_scientist.agents.base_agent import BaseResearchAgent, project_snapshot
from src.ai_scientist.schemas import ReproducibilityOutput, ResearchProject


class ReproducibilityEngineerAgent(BaseResearchAgent[ReproducibilityOutput]):
    agent_name = "reproducibility_engineer"
    output_model = ReproducibilityOutput

    def build_payload(self, project: ResearchProject) -> dict:
        return project_snapshot(project, [
            "research_mode",
            "study_design",
            "analysis_plan",
            "reproducibility_seed",
            "workflow_version",
            "available_tools",
            "artifacts",
        ])
