"""Formal research-plan report generation for AI Scientist V0.2."""

from __future__ import annotations

from typing import Any

from src.ai_scientist.quality import source_level_distribution
from src.ai_scientist.report_renderer import (
    render_analysis_plan_markdown,
    render_claims_markdown,
    render_evidence_markdown,
    render_evidence_curation_markdown,
    render_reproducibility_markdown,
    render_review_markdown,
    render_study_design_markdown,
    render_todos_markdown,
)
from src.ai_scientist.schemas import ResearchProject


NO_EXECUTION_STATEMENT = (
    "This report is a research plan produced through AI Scientist multi-role planning and review. "
    "No real experiment, simulation, or data analysis has been executed, so it must not be treated "
    "as an experimental conclusion."
)


def build_research_plan_json(project: ResearchProject) -> dict[str, Any]:
    """Return a display-safe, auditable research-plan object."""

    review = project.reviews[-1].model_dump(mode="json") if project.reviews else None
    return {
        "project_id": project.project_id,
        "title": project.title,
        "status": project.phase.value,
        "statement": NO_EXECUTION_STATEMENT,
        "research_question": project.question.model_dump(mode="json") if project.question else None,
        "scope": project.question.scope if project.question else "",
        "operational_definitions": project.question.operational_definitions if project.question else [],
        "success_criteria": project.question.measurable_success_criteria if project.question else [],
        "evidence": [item.model_dump(mode="json") for item in project.evidence],
        "evidence_curation": {
            "review_mode": project.evidence_review_mode,
            "search_plan_versions": len(project.search_plan_history),
            "candidate_count": len(project.source_candidate_collections[-1].candidates)
            if project.source_candidate_collections else 0,
            "selection_snapshots": [
                item.model_dump(mode="json") for item in project.source_selection_snapshots
            ],
            "feedback_summary": project.source_review_feedback.model_dump(mode="json"),
        },
        "claims": [item.model_dump(mode="json") for item in project.claims],
        "quality_metrics": project.quality_metrics.model_dump(mode="json"),
        "source_level_distribution": source_level_distribution(project.evidence),
        "hypotheses": [item.model_dump(mode="json") for item in project.hypotheses],
        "method_selection": {
            "research_mode": project.research_mode.value if project.research_mode else None,
            "rationale": project.method_rationale,
            "validity_threats": project.validity_threats,
            "required_controls": project.required_controls,
        },
        "study_design": project.study_design.model_dump(mode="json") if project.study_design else None,
        "analysis_plan": project.analysis_plan.model_dump(mode="json") if project.analysis_plan else None,
        "reproducibility_plan": project.reproducibility_plan,
        "reviewer": review,
        "conclusion": project.conclusion.model_dump(mode="json") if project.conclusion else None,
        "unknowns": project.question.unknowns if project.question else [],
        "human_actions_required": project.human_actions_required,
        "missing_capabilities": project.missing_capabilities,
    }


def build_research_plan_markdown(project: ResearchProject) -> str:
    """Create the required V0.2 markdown report."""

    data = build_research_plan_json(project)
    metrics = data["quality_metrics"]
    source_dist = data["source_level_distribution"]
    lines = [
        f"# {project.title}",
        "",
        "## 1. Project Summary",
        NO_EXECUTION_STATEMENT,
        "",
        f"- Project ID: `{project.project_id}`",
        f"- Status: `{project.phase.value}`",
        f"- Research mode: `{project.research_mode.value if project.research_mode else 'pending'}`",
        "",
        "## 2. Research Question",
        _question_text(project),
        "",
        "## 3. Scope and Operational Definitions",
        _bullet_list(project.question.operational_definitions if project.question else []),
        "",
        "## 4. Success Criteria",
        _bullet_list(project.question.measurable_success_criteria if project.question else []),
        "",
        "## 5. Background Evidence",
        render_evidence_markdown(project),
        "",
        "## Evidence Curation",
        render_evidence_curation_markdown(project),
        "",
        "## 6. Claim-Evidence Mapping",
        render_claims_markdown(project),
        "",
        "## 7. Evidence Quality Metrics",
        "\n".join(
            [
                f"- Evidence coverage: `{metrics['evidence_coverage']}`",
                f"- Primary source ratio: `{metrics['primary_source_ratio']}`",
                f"- Unverifiable source count: `{metrics['unverifiable_source_count']}`",
                f"- Source levels: `{source_dist}`",
            ]
        ),
        "",
        "## 8. Hypotheses",
        _hypothesis_table(project),
        "",
        "## 9. Competing Explanations",
        _bullet_list([alt for hyp in project.hypotheses for alt in hyp.alternative_explanations]),
        "",
        "## 10. Method Selection",
        f"{project.method_rationale or 'Not yet selected.'}",
        "",
        "## 11. Study Design",
        render_study_design_markdown(project.study_design),
        "",
        "## 12. Analysis Plan",
        render_analysis_plan_markdown(project.analysis_plan),
        "",
        "## 13. Reproducibility Plan",
        render_reproducibility_markdown(project.reproducibility_plan),
        "",
        "## 14. Risks, Bias, and Ethics",
        _bullet_list((project.validity_threats or []) + (project.study_design.ethical_considerations if project.study_design else [])),
        "",
        "## 15. Reviewer Scores and Comments",
        render_review_markdown(project.reviews[-1] if project.reviews else None),
        "",
        "## 16. Unknown Questions",
        _bullet_list(project.question.unknowns if project.question else []),
        "",
        "## 17. Human or External Tool To-Dos",
        render_todos_markdown(project),
        "",
        "## 18. Research Status Statement",
        NO_EXECUTION_STATEMENT,
        "",
        "## Quality Summary",
        "\n".join(
            [
                f"- Hypothesis completeness: `{metrics['hypothesis_completeness']}`",
                f"- Conclusion traceability: `{metrics['conclusion_traceability']}`",
                f"- Reviewer minimum score: `{metrics['reviewer_min_score']}`",
                f"- Blocking issue count: `{metrics['blocking_issue_count']}`",
            ]
        ),
        "",
    ]
    return "\n".join(lines)


def _question_text(project: ResearchProject) -> str:
    if not project.question:
        return "No formal research question has been generated."
    return f"**Normalized question:** {project.question.normalized_question}\n\n**Scope:** {project.question.scope}"


def _evidence_table(project: ResearchProject) -> str:
    if not project.evidence:
        return "No evidence records are available."
    rows = ["| ID | Level | Primary | Verified | Title | URL |", "|---|---|---:|---:|---|---|"]
    for item in project.evidence:
        rows.append(
            f"| `{item.evidence_id}` | {item.source_level} | {item.is_primary_source} | "
            f"{item.verified} | {item.title} | {item.source_url or ''} |"
        )
    return "\n".join(rows)


def _claim_table(project: ResearchProject) -> str:
    if not project.claims:
        return "No claim records are available."
    rows = ["| ID | Status | Statement | Supporting Evidence |", "|---|---|---|---|"]
    for item in project.claims:
        rows.append(
            f"| `{item.claim_id}` | {item.status} | {item.statement} | "
            f"{', '.join(item.supporting_evidence_ids)} |"
        )
    return "\n".join(rows)


def _hypothesis_table(project: ResearchProject) -> str:
    if not project.hypotheses:
        return "No hypotheses are available."
    rows = ["| ID | Statement | Predictions | Falsification |", "|---|---|---|---|"]
    for item in project.hypotheses:
        rows.append(
            f"| `{item.hypothesis_id}` | {item.statement} | "
            f"{'; '.join(item.predictions)} | {'; '.join(item.falsification_conditions)} |"
        )
    return "\n".join(rows)


def _bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- Not specified."
