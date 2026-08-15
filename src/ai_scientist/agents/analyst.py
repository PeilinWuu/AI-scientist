"""Analysis planning role; it never executes analysis in this release."""

from src.ai_scientist.agents.base_agent import BaseResearchAgent, project_snapshot
from src.ai_scientist.schemas import AnalysisPlan, ResearchProject


class AnalystAgent(BaseResearchAgent[AnalysisPlan]):
    agent_name = "analyst"
    output_model = AnalysisPlan

    def build_payload(self, project: ResearchProject) -> dict:
        payload = project_snapshot(project, ["question", "hypotheses", "study_design", "constraints", "artifacts"])
        payload["execution_boundary"] = "Create a plan only. No analysis has been executed in this stage."
        return payload
