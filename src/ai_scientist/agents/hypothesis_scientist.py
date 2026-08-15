"""Falsifiable hypothesis generation role."""

from src.ai_scientist.agents.base_agent import BaseResearchAgent, project_snapshot
from src.ai_scientist.schemas import HypothesisOutput, ResearchProject


class HypothesisScientistAgent(BaseResearchAgent[HypothesisOutput]):
    agent_name = "hypothesis_scientist"
    output_model = HypothesisOutput

    def build_payload(self, project: ResearchProject) -> dict:
        return project_snapshot(project, ["question", "evidence", "claims", "evidence_gaps", "conflicting_evidence"])
