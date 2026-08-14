"""Natural-language Markdown renderers for auditable research products."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from src.ai_scientist.schemas import AnalysisPlan, ResearchProject, ReviewResult, StudyDesign


def bullets(items: Iterable[Any], empty: str = "尚未明确。") -> str:
    values = _dedupe_text(items)
    return "\n".join(f"- {item}" for item in values) if values else f"- {empty}"


def render_study_design_markdown(design: StudyDesign | None) -> str:
    if design is None:
        return "研究设计尚未生成。"
    sections = [
        f"**研究目标：** {design.objective or '尚未明确。'}",
        f"**研究对象或系统：** {design.population_or_system or '尚未明确。'}",
        "**待检验假设**\n" + bullets(design.hypotheses_tested),
        "**变量**\n" + bullets(design.variables),
        "**对照与比较组**\n" + bullets([*design.controls, *design.comparison_groups]),
        "**采样与数据收集**\n" + bullets([*design.sampling_plan, *design.data_collection_plan]),
        "**测量方案**\n" + bullets(design.measurement_plan),
        "**质量控制与停止规则**\n" + bullets([*design.quality_controls, *design.stopping_rules]),
        f"**可行性判断：** {design.feasibility or '尚未评估。'}",
        "**风险与伦理事项**\n" + bullets([*design.risks, *design.ethical_considerations]),
    ]
    return "\n\n".join(sections)


def render_analysis_plan_markdown(plan: AnalysisPlan | None) -> str:
    if plan is None:
        return "分析方案尚未生成。"
    sections = [
        "**分析目标**\n" + bullets(plan.objectives),
        "**输入数据与预处理**\n" + bullets([*plan.input_data, *plan.preprocessing]),
        "**评价指标**\n" + bullets(plan.metrics),
        "**统计假设与方法**\n" + bullets([*plan.statistical_assumptions, *plan.statistical_methods]),
        "**稳健性与敏感性分析**\n" + bullets([*plan.robustness_checks, *plan.sensitivity_analysis]),
        "**不确定性量化**\n" + bullets(plan.uncertainty_quantification),
        "**可视化方案**\n" + bullets(plan.visualization_plan),
        "**成功与失败判据**\n" + bullets([*plan.success_criteria, *plan.failure_criteria]),
    ]
    return "\n\n".join(sections)


def render_reproducibility_markdown(plan: Mapping[str, Any] | None) -> str:
    if not plan:
        return "可复现性方案尚未生成。"
    labels = {
        "environment_specification": "运行环境",
        "software_and_versions": "软件与版本",
        "randomness_controls": "随机性控制",
        "data_provenance": "数据来源与谱系",
        "artifact_retention": "产物留存",
        "reproduction_steps": "复现步骤",
        "validation_checks": "验证检查",
        "reproducibility_plan": "复现计划",
        "missing_reproducibility_information": "尚缺信息",
    }
    rendered = []
    for key, value in plan.items():
        title = labels.get(str(key), str(key).replace("_", " ").title())
        values = value if isinstance(value, list) else [value]
        rendered.append(f"**{title}**\n{bullets(values)}")
    return "\n\n".join(rendered)


def render_review_markdown(review: ReviewResult | None) -> str:
    if review is None:
        return "独立审查尚未完成。"
    decision = {
        "approve": "通过，可提交人工审批",
        "reject": "否决",
        "revise_question": "需修订研究问题",
        "revise_evidence": "需补充证据",
        "revise_hypothesis": "需修订假设",
        "revise_method": "需修订研究方法",
        "revise_design": "需修订研究设计",
        "revise_analysis": "需修订分析方案",
    }.get(review.decision, "需进一步审查")
    scores = [
        f"证据质量 {review.evidence_quality_score:.1f}/10",
        f"方法有效性 {review.methodological_validity_score:.1f}/10",
        f"可行性 {review.feasibility_score:.1f}/10",
        f"可复现性 {review.reproducibility_score:.1f}/10",
        f"主张支持度 {review.claim_support_score:.1f}/10",
        f"不确定性处理 {review.uncertainty_handling_score:.1f}/10",
    ]
    return "\n\n".join([
        f"**审查结论：** {decision}",
        "**评分**\n" + bullets(scores),
        "**阻断问题**\n" + bullets(review.blocking_issues, "无阻断问题。"),
        "**非阻断问题**\n" + bullets(review.non_blocking_issues, "无。"),
        "**建议**\n" + bullets(review.recommendations),
        "**批准条件**\n" + bullets(review.approval_conditions, "无额外条件。"),
    ])


def render_evidence_markdown(project: ResearchProject) -> str:
    if not project.evidence:
        return "尚未保留可验证证据。"
    rows = ["| 来源 | 等级 | 验证状态 | 主要结论 |", "|---|---|---|---|"]
    for item in project.evidence:
        title = _escape_table(item.title)
        source = f"[{title}]({item.source_url})" if item.source_url else title
        summary = _escape_table(item.summary)
        verified = "已验证" if item.verified else "待验证"
        rows.append(f"| {source} | {item.source_level} | {verified} | {summary} |")
    return "\n".join(rows)


def render_claims_markdown(project: ResearchProject) -> str:
    if not project.claims:
        return "尚未形成主张与证据映射。"
    rows = ["| 主张 | 状态 | 支持证据 | 反驳证据 |", "|---|---|---|---|"]
    for claim in project.claims:
        rows.append(
            f"| {_escape_table(claim.statement)} | {claim.status} | "
            f"{', '.join(claim.supporting_evidence_ids) or '无'} | "
            f"{', '.join(claim.contradicting_evidence_ids) or '无'} |"
        )
    return "\n".join(rows)


def render_evidence_curation_markdown(project: ResearchProject) -> str:
    candidates = (
        project.source_candidate_collections[-1].candidates
        if project.source_candidate_collections else []
    )
    selection = project.source_selection_snapshots[-1] if project.source_selection_snapshots else None
    recommended = len([item for item in candidates if item.ai_recommendation == "keep"])
    kept = len(selection.kept_candidate_ids) if selection else 0
    rejected = len(selection.rejected_candidate_ids) if selection else 0
    deferred = len(selection.deferred_candidate_ids) if selection else 0
    verified = len([
        item for item in project.evidence
        if item.verification_status in {"verified", "partially_verified"} and not item.duplicate_of
    ])
    lines = [
        f"系统检索并整理了 **{len(candidates)}** 个候选来源，AI 初步建议保留 **{recommended}** 个。",
        f"研究者最终保留 **{kept}** 个、排除 **{rejected}** 个、暂缓 **{deferred}** 个。",
        f"其中 **{verified}** 个来源通过来源验证并形成正式 Evidence Collection。",
    ]
    if project.source_review_feedback.concise_feedback:
        lines.extend(["", "**人工排除理由汇总**", bullets(project.source_review_feedback.concise_feedback)])
    lines.extend([
        "",
        "每条正式证据通过 selection provenance 连接候选来源、人工选择快照和验证方法。",
    ])
    return "\n".join(lines)


def classify_and_dedupe_todos(project: ResearchProject) -> dict[str, list[str]]:
    """Classify execution limitations without turning them into fake results."""

    return {
        "人工操作": _dedupe_text(project.human_actions_required),
        "待接入能力": _dedupe_text(project.missing_capabilities),
        "待解决证据缺口": _dedupe_text(project.evidence_gaps),
    }


def render_todos_markdown(project: ResearchProject) -> str:
    sections = []
    for title, values in classify_and_dedupe_todos(project).items():
        sections.append(f"**{title}**\n{bullets(values, '无。')}")
    return "\n\n".join(sections)


def _dedupe_text(items: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        key = " ".join(text.lower().split())
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _escape_table(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")
