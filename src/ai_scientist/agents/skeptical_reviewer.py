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
                "computation",
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
                    "Re-check approved issues against current artifacts. Distinguish optional scope expansion from "
                    "genuine scientific defects in the current question. Scientific defects remain blocking even "
                    "after a previous revision passed. Preserve uncertainty; no forced passing scores."
                ),
            }
            if project.internal_execution_summary:
                payload['review_scope'] = {
                    'type': 'execution_result_review',
                    'revision_plan_id': latest_plan.revision_plan_id if latest_plan else None,
                    'rule': (
                        'Independently evaluate the actual execution against the approved question, design and '
                        'analysis plan. Inspect numerical/statistical validation, leakage, missing controls, '
                        'reproducibility and whether claims are supported by actual metrics and source content. '
                        'A successful program exit or a matched quotation is not scientific proof. '
                        'Scientific validity failures are blocking; optional new scope is not. '
                        'Route actual code defects to execution_code, missing numerical outputs to result_table, '
                        'unreadable plots to figure, and runnable source/environment delivery to reproducibility_bundle. '
                        'Route missing or incorrect written reports to report, NEVER analysis_plan. '
                        'A report draft in computation.delivery_report is a saved real file, not a plan. '
                        'These targets trigger an approved real execution repair, NOT a rewrite of a planning document. '
                        'Do not claim pictures have been inspected when only file metadata is available. '
                        'Also check closure of any human-approved revision issues. Accept valid negative or '
                        'inconclusive findings without demanding a positive result.'
                    ),
                }
        return payload
