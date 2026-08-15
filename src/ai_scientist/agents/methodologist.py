"""Methodological validity role."""

from src.ai_scientist.agents.base_agent import BaseResearchAgent, project_snapshot
from src.ai_scientist.schemas import MethodologyOutput, ResearchProject


class MethodologistAgent(BaseResearchAgent[MethodologyOutput]):
    agent_name = "methodologist"
    output_model = MethodologyOutput

    def build_payload(self, project: ResearchProject) -> dict:
        return project_snapshot(
            project,
            ["question", "research_mode", "secondary_modes", "constraints", "available_tools", "missing_capabilities"],
        )
