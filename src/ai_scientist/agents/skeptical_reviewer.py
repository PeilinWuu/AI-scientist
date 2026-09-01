"""Independent review role receiving structured project state only."""

from src.ai_scientist.agents.base_agent import BaseResearchAgent, project_snapshot
from src.ai_scientist.schemas import ResearchProject, ReviewResult


class SkepticalReviewerAgent(BaseResearchAgent[ReviewResult]):
    agent_name = "skeptical_reviewer"
    output_model = ReviewResult

    def build_payload(self, project: ResearchProject) -> dict:
        payload = project_snapshot(
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
                "planning_only",
                "execution_requirements",
                "accepted_limitations",
                "internal_execution_summary",
                "controlled_python_runs",
                "approved_revision_plans",
                "revision_verifications",
            ],
        )
        if project.phase.value == "CRITICAL_REVIEW":
            completed_plans = [item for item in project.approved_revision_plans if item.status == "completed"]
            latest_plan = completed_plans[-1] if completed_plans else None
            payload["review_scope"] = {
                "type": "bounded_revision_closure",
                "revision_plan_id": latest_plan.revision_plan_id if latest_plan else None,
                "rule": (
                    "Re-check closure of the human-approved blocking issues. New improvements are non-blocking "
                    "unless they identify fabricated evidence or data, a safety or ethics violation, or a "
                    "fundamentally unresearchable question. Do not create an endless sequence of new standards."
                ),
            }
        return payload
