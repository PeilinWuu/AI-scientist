"""Research question formalization role."""

from src.ai_scientist.agents.base_agent import BaseResearchAgent, project_snapshot
from src.ai_scientist.schemas import DirectorOutput, ResearchProject


class ResearchDirectorAgent(BaseResearchAgent[DirectorOutput]):
    agent_name = "research_director"
    output_model = DirectorOutput

    def build_payload(self, project: ResearchProject) -> dict:
        return project_snapshot(project, ["objective", "constraints", "domain_hint", "revision_feedback"])
