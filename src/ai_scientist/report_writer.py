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

EXECUTION_STATEMENT = (
    "This report includes project-internal controlled execution results. Input hashes, parameters, "
    "software versions, execution identifiers, and output checksums were saved for audit."
)


def build_research_plan_json(project: ResearchProject) -> dict[str, Any]:
    """Return a display-safe, auditable research-plan object."""

    review = project.reviews[-1].model_dump(mode="json") if project.reviews else None
    return {
        "project_id": project.project_id,
        "title": project.title,
        "status": project.phase.value,
        "workflow_version": project.workflow_version,
        "reproducibility_seed": project.reproducibility_seed,
        "statement": _research_status_statement(project),
        "uploaded_assets": [
            {
                "asset_id": asset.asset_id,
                "filename": asset.filename,
                "purpose": asset.purpose,
                "asset_role": asset.asset_role,
                "research_round": asset.research_round,
                "source": asset.source,
                "description": asset.description,
                "parsing_status": asset.parsing_status,
                "parser_name": asset.parsed_content.parser_name if asset.parsed_content else None,
                "summary": asset.parsed_content.summary if asset.parsed_content else "",
                "content_sha256": asset.parsed_content.content_sha256 if asset.parsed_content else None,
                "warnings": asset.parsed_content.warnings if asset.parsed_content else [],
                "used_by_agents": asset.used_by_agents,
            }
            for asset in project.research_assets
        ],
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
        "internal_execution_summary": project.internal_execution_summary,
        "controlled_python_runs": project.controlled_python_runs,
        "reviewer": review,
        "revision_workflow": {
            "issues": [item.model_dump(mode="json") for item in project.revision_issues],
            "approved_plans": [item.model_dump(mode="json") for item in project.approved_revision_plans],
            "verifications": [item.model_dump(mode="json") for item in project.revision_verifications],
            "execution_requirements": project.execution_requirements,
            "accepted_limitations": project.accepted_limitations,
        },
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
        data["statement"],
        "",
        f"- Project ID: `{project.project_id}`",
        f"- Status: `{project.phase.value}`",
        f"- Research mode: `{project.research_mode.value if project.research_mode else 'pending'}`",
        f"- Workflow version: `{project.workflow_version}`",
        f"- Reproducibility seed: `{project.reproducibility_seed if project.reproducibility_seed is not None else 'Auto'}`",
        "",
        "## Uploaded Research Materials and Data",
        _uploaded_assets(project),
        "",
        "## Controlled Execution Results",
        _execution_summary_markdown(project),
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
        "## 16. Independent Review and Revision History",
        _revision_history(project),
        "",
        "## 17. Unknown Questions",
        _bullet_list(project.question.unknowns if project.question else []),
        "",
        "## 18. Human or External Tool To-Dos",
        render_todos_markdown(project),
        "",
        "## 19. Research Status Statement",
        data["statement"],
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


def _research_status_statement(project: ResearchProject) -> str:
    if _execution_completed(project):
        if project.conclusion and project.conclusion.planning_status_statement:
            return project.conclusion.planning_status_statement
        return EXECUTION_STATEMENT
    return NO_EXECUTION_STATEMENT


def _execution_completed(project: ResearchProject) -> bool:
    return project.internal_execution_summary.get("status") == "complete" or any(
        item.get("status") == "success" for item in project.controlled_python_runs
    )


def _execution_summary_markdown(project: ResearchProject) -> str:
    if not _execution_completed(project):
        return "No controlled execution result is registered for this project."
    summary = project.internal_execution_summary
    lines = [
        f"- Executor: `{summary.get('executor_binding') or project.executor_binding or 'controlled_python'}`",
    ]
    if summary.get("run_id"):
        lines.append(f"- Run ID: `{summary['run_id']}`")
    if summary.get("seed") is not None:
        lines.append(f"- Seed: `{summary['seed']}`")
    if summary.get("observation_asset_id") or summary.get("dataset_asset_id"):
        lines.append(
            f"- Input asset: `{summary.get('observation_asset_id') or summary.get('dataset_asset_id')}`"
        )

    round_1 = summary.get("round_1") or {}
    round_2 = summary.get("round_2") or {}
    if round_1 and round_2:
        lines.extend(
            [
                "",
                "| Round | Damping range | Omega range | Best damping | Best omega | RMSE | Evaluations | Execution ID |",
                "|---|---|---|---:|---:|---:|---:|---|",
                _execution_round_row("1", round_1),
                _execution_round_row("2", round_2),
            ]
        )
        iteration = (summary.get("comparison") or {}).get("iteration") or {}
        if iteration:
            lines.extend(
                [
                    "",
                    f"- Absolute RMSE gain: `{iteration.get('absolute_rmse_gain')}`",
                    f"- Relative RMSE gain: `{iteration.get('relative_rmse_gain_percent')}%`",
                    f"- Total two-round evaluations: `{int(iteration.get('round_1_evaluations', 0)) + int(iteration.get('round_2_evaluations', 0))}`",
                ]
            )
        adjustments = summary.get("plan_adjustments") or []
        if adjustments:
            lines.append(f"- Round 2 adjustment: {adjustments[0].get('reason', 'Recorded in execution provenance.')}")
    elif summary.get("operations"):
        lines.append(f"- Completed allowlisted operations: `{len(summary['operations'])}`")
        lines.append(f"- Arbitrary code execution: `{summary.get('arbitrary_code_execution', False)}`")
    if project.controlled_python_runs:
        successful = len([item for item in project.controlled_python_runs if item.get("status") == "success"])
        lines.append(f"- Successful controlled Python runs: `{successful}`")
    return "\n".join(lines)


def _execution_round_row(label: str, round_result: dict[str, Any]) -> str:
    parameters = round_result.get("actual_parameters") or {}
    metrics = round_result.get("metrics") or {}
    damping_range = f"[{parameters.get('damping_min')}, {parameters.get('damping_max')}]"
    omega_range = f"[{parameters.get('omega_min')}, {parameters.get('omega_max')}]"
    return (
        f"| {label} | `{damping_range}` | `{omega_range}` | {metrics.get('best_damping')} | "
        f"{metrics.get('best_omega')} | {metrics.get('rmse')} | {metrics.get('evaluations')} | "
        f"`{round_result.get('execution_id', '')}` |"
    )


def _uploaded_assets(project: ResearchProject) -> str:
    if not project.research_assets:
        return "No user-provided research files were registered."
    lines = [
        "Parsing creates a bounded local representation; it does not independently verify a reference or execute data analysis.",
        "",
    ]
    for asset in project.research_assets:
        summary = asset.parsed_content.summary if asset.parsed_content else "No parsed summary available."
        used_by = ", ".join(asset.used_by_agents) if asset.used_by_agents else "not yet used by a research role"
        digest = asset.parsed_content.content_sha256 if asset.parsed_content else "unavailable"
        lines.append(
            f"- `{asset.asset_id}` **{asset.filename}** ({asset.purpose}, {asset.parsing_status}): "
            f"{summary} Used by: {used_by}. SHA-256: `{digest}`."
        )
    return "\n".join(lines)


def _revision_history(project: ResearchProject) -> str:
    if not project.approved_revision_plans:
        return "No human-approved revision cycle has been executed."
    verification_by_id = {item.verification_id: item for item in project.revision_verifications}
    lines: list[str] = []
    for plan in project.approved_revision_plans:
        lines.extend(
            [
                f"### Review v{plan.review_version} / Revision cycle {plan.revision_cycle}",
                f"- Accepted: `{len(plan.approved_issues)}`",
                f"- Deferred to execution: `{len(plan.deferred_issues)}`",
                f"- Accepted as limitations: `{len(plan.accepted_as_limitation)}`",
                f"- Rejected by human reviewer: `{len(plan.rejected_issues)}`",
            ]
        )
        for batch in plan.target_batches:
            verification = verification_by_id.get(batch.verification_id or "")
            passed = len([item for item in verification.criteria_results if item.passed]) if verification else 0
            total = len(verification.criteria_results) if verification else 0
            lines.append(
                f"- `{batch.target}` v{batch.new_artifact_version or 'pending'}: "
                f"`{batch.status}`, verification `{passed}/{total}`"
            )
        lines.append("")
    return "\n".join(lines).strip()


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
