"""Independent review role receiving structured project state only."""

from src.ai_scientist.agents.base_agent import BaseResearchAgent, project_snapshot
from src.ai_scientist.schemas import ResearchProject, ReviewResult


class SkepticalReviewerAgent(BaseResearchAgent[ReviewResult]):
    agent_name = "skeptical_reviewer"
    output_model = ReviewResult

    def build_payload(self, project: ResearchProject) -> dict:
        return project_snapshot(
            project,
            [
                "question",
                "evidence",
                "claims",
                "hypotheses",
                "study_design",
                "analysis_plan",
                "reproducibility_plan",
                "missing_capabilities",
                "human_actions_required",
            ],
        )
