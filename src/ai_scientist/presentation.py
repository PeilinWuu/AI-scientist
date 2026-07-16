"""Natural-language presentation helpers for AI Scientist UI."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.ai_scientist.quality import source_level_distribution
from src.ai_scientist.schemas import (
    AnalysisPlan,
    Conclusion,
    DomainSelectionOutput,
    EvidenceItem,
    Hypothesis,
    MethodologyOutput,
    ResearchEvent,
    ResearchMode,
    ResearchProject,
    ResearchQuestion,
    ReviewResult,
    StudyDesign,
)


PHASE_LABELS = {
    "INTAKE": "创建研究项目",
    "QUESTION_FORMULATION": "理解研究问题",
    "RESEARCH_MODE_SELECTION": "选择研究方法",
    "DOMAIN_SELECTION": "判断研究领域",
    "BACKGROUND_RESEARCH": "检索和评估证据",
    "CLAIM_EVIDENCE_MAPPING": "整理主张与证据",
    "HYPOTHESIS_GENERATION": "形成假设与竞争性解释",
    "METHOD_SELECTION": "细化研究方法",
    "STUDY_DESIGN": "设计研究方案",
    "ANALYSIS_PLANNING": "设计分析方案",
    "FEASIBILITY_REVIEW": "独立审查与修订",
    "HUMAN_APPROVAL": "等待人工批准",
    "SYNTHESIS": "形成研究方案",
    "COMPLETED": "研究方案完成",
}


def render_research_question(question: ResearchQuestion | None) -> str:
    if question is None:
        return "研究总监还没有完成问题整理。"
    missing = f" 仍需明确：{'；'.join(question.missing_information)}。" if question.missing_information else ""
    criteria = f" 成功标准包括：{'；'.join(question.measurable_success_criteria[:3])}。" if question.measurable_success_criteria else ""
    return f"研究总监已将目标整理为可研究问题：**{question.normalized_question}**。研究范围是：{question.scope or '尚待细化'}。{criteria}{missing}"


def render_mode_selection(mode: ResearchMode | None, rationale: str = "") -> str:
    if mode is None:
        return "方法学专家尚未完成研究模式判断。"
    return f"方法学专家认为当前问题更适合采用 **{_mode_label(mode)}**。{rationale or '后续阶段会继续细化可执行的研究设计。'}"


def render_domain_selection(domain: str, secondary_domains: list[str] | None = None) -> str:
    secondary = f" 同时需要参考：{'、'.join(secondary_domains)}。" if secondary_domains else ""
    return f"研究领域判断为 **{_domain_label(domain)}**。{secondary}"


def render_evidence_summary(evidence: list[EvidenceItem], gaps: list[str] | None = None) -> str:
    if not evidence:
        return "证据研究员尚未保留可验证证据。可以重试检索，或先补充更具体的研究对象和数据来源。"
    levels = source_level_distribution(evidence)
    primary = len([item for item in evidence if item.is_primary_source])
    return (
        f"证据研究员完成了第一轮背景检索，保留 **{len(evidence)}** 条证据，其中 **{primary}** 条为一级来源。"
        f"来源等级分布为 A:{levels['A']}、B:{levels['B']}、C:{levels['C']}、D:{levels['D']}、E:{levels['E']}。"
        f"{' 主要证据缺口：' + '；'.join((gaps or [])[:3]) if gaps else ''}"
    )


def render_claim_mapping(project: ResearchProject) -> str:
    supported = len([item for item in project.claims if item.status in {"supported", "partially_supported"}])
    unsupported = len(project.claims) - supported
    return f"主张与证据整理完成：当前共有 **{len(project.claims)}** 条关键主张，其中 **{supported}** 条有证据支持，**{unsupported}** 条仍需补证或降级表述。"


def render_hypotheses(hypotheses: list[Hypothesis]) -> str:
    if not hypotheses:
        return "假设科学家尚未形成可检验假设。"
    falsifiable = len([item for item in hypotheses if item.falsification_conditions])
    return f"假设科学家形成了 **{len(hypotheses)}** 个候选假设，其中 **{falsifiable}** 个已经写明可证伪条件。"


def render_method_selection(output: MethodologyOutput | None, project: ResearchProject | None = None) -> str:
    if output is None and project is not None:
        return render_mode_selection(project.research_mode, project.method_rationale)
    if output is None:
        return "方法学专家尚未完成方法细化。"
    return f"方法学专家选择 **{_mode_label(output.selected_research_mode)}**，主要理由是：{output.methodological_rationale}"


def render_study_design(design: StudyDesign | None) -> str:
    if design is None:
        return "研究设计尚未生成。"
    return f"研究设计师已形成方案：研究对象为 **{design.population_or_system or '待明确'}**，包含 **{len(design.variables)}** 个变量、**{len(design.controls)}** 项控制和 **{len(design.quality_controls)}** 项质量控制。"


def render_analysis_plan(plan: AnalysisPlan | None) -> str:
    if plan is None:
        return "分析方案尚未生成。"
    return f"分析专家已设计分析方案：包含 **{len(plan.metrics)}** 个指标、**{len(plan.statistical_methods)}** 类方法和 **{len(plan.robustness_checks)}** 项稳健性检查。"


def render_feasibility_review(review: ReviewResult | None) -> str:
    if review is None:
        return "独立审查尚未完成。"
    if review.decision == "approve":
        return "独立审查员认为当前研究方案可以进入人工批准。"
    target = review.required_revision_target if review.required_revision_target != "none" else "相关部分"
    return f"独立审查员认为当前方案仍需修订，建议回到 **{target}**。主要阻断问题：{'；'.join(review.blocking_issues[:3]) or '质量门槛未完全通过'}。"


def render_review(review: ReviewResult | None) -> str:
    return render_feasibility_review(review)


def render_synthesis(conclusion: Conclusion | None) -> str:
    if conclusion is None:
        return "最终研究方案尚未生成。"
    return f"科学综合已完成，形成了 **{len(conclusion.supported_findings)}** 条可追溯发现和 **{len(conclusion.next_questions)}** 个后续问题。注意：这仍是研究方案，不是实验结论。"


def render_event(event: ResearchEvent) -> str:
    time_text = _time(event.started_at)
    phase = PHASE_LABELS.get(event.phase.value, event.phase.value)
    if event.status == "running":
        return f"{time_text}　{phase}开始。"
    if event.status == "failed":
        detail = event.error_message or event.error or "阶段执行失败，项目保留在上一完整阶段。"
        return f"{time_text}　{phase}失败：{detail}"
    if event.display_markdown:
        return f"{time_text}　{event.display_markdown}"
    return f"{time_text}　{phase}完成。"


def render_event_dict(event: dict[str, Any]) -> str:
    phase_value = str(event.get("phase", ""))
    time_text = _time_from_string(event.get("started_at"))
    phase = PHASE_LABELS.get(phase_value, phase_value)
    status = event.get("status")
    if status == "running":
        return f"{time_text}　{phase}开始。"
    if status == "failed":
        detail = event.get("display_markdown") or event.get("error_message") or "阶段执行失败，项目保留在上一完整阶段。"
        return f"{time_text}　{detail}"
    if event.get("summary_markdown"):
        return f"{time_text}　{event['summary_markdown']}"
    if event.get("display_markdown"):
        return f"{time_text}　{event['display_markdown']}"
    return f"{time_text}　{phase}完成。"


def render_capabilities(debug: bool = False, raw: dict[str, Any] | None = None) -> str:
    if debug and raw:
        return "\n".join(f"- {key}: {value}" for key, value in raw.items())
    return "\n".join(
        [
            "**当前可用**：联网检索、网页读取、产物保存。",
            "",
            "**尚未接入**：文件分析、Python 执行、统计分析、代码运行。",
        ]
    )


def render_project_overview(project: ResearchProject | dict[str, Any]) -> str:
    data = project.model_dump(mode="json") if hasattr(project, "model_dump") else project
    phase = PHASE_LABELS.get(str(data.get("phase", "")), str(data.get("phase", "")))
    calls = (data.get("budget") or {}).get("successful_model_calls", (data.get("budget") or {}).get("used_model_calls", 0))
    return f"当前处于 **{phase}**。本项目已完成 **{calls}** 次模型协作调用。"


def _mode_label(mode: ResearchMode | str) -> str:
    value = mode.value if isinstance(mode, ResearchMode) else str(mode)
    return {
        "observational": "观察性研究",
        "mixed_methods": "混合方法研究",
        "data_analysis": "数据分析研究",
        "systematic_review": "系统综述",
        "controlled_experiment": "受控实验",
        "computational_experiment": "计算实验",
        "simulation": "仿真研究",
        "engineering_design": "工程设计研究",
        "theoretical": "理论研究",
    }.get(value, value)


def _domain_label(domain: str) -> str:
    return {
        "social_science": "社会科学",
        "computer_science": "计算机科学",
        "biology": "生命科学",
        "engineering": "工程",
        "general": "通用研究",
    }.get(domain, domain)


def _time(value: datetime | None) -> str:
    return value.strftime("%H:%M") if value else "--:--"


def _time_from_string(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return "--:--"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%H:%M")
    except ValueError:
        return "--:--"
