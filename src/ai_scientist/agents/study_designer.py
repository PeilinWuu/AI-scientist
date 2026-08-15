"""Research-mode-sensitive study design role."""

from src.ai_scientist.agents.base_agent import BaseResearchAgent, project_snapshot
from src.ai_scientist.schemas import ResearchProject, StudyDesign


class StudyDesignerAgent(BaseResearchAgent[StudyDesign]):
    agent_name = "study_designer"
    output_model = StudyDesign

    def build_payload(self, project: ResearchProject) -> dict:
        return project_snapshot(
            project,
            [
                "question",
                "research_mode",
                "hypotheses",
                "constraints",
                "available_tools",
                "missing_capabilities",
                "validity_threats",
                "required_controls",
            ],
        )
