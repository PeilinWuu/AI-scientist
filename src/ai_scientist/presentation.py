"""Natural-language presentation helpers for AI Scientist UI."""

from __future__ import annotations

import re
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
from src.ui_time import format_local_time


PHASE_LABELS = {
    "INTAKE": "创建研究项目",
    "QUESTION_FORMULATION": "理解研究问题",
    "RESEARCH_MODE_SELECTION": "选择研究方法",
    "DOMAIN_SELECTION": "判断研究领域",
    "BACKGROUND_RESEARCH": "检索和评估证据",
    "SEARCH_PLAN_REVIEW": "审核检索方案",
    "HUMAN_SOURCE_REVIEW": "候选资料审查",
    "CLAIM_EVIDENCE_MAPPING": "整理主张与证据",
    "HYPOTHESIS_GENERATION": "形成假设与竞争性解释",
    "METHOD_SELECTION": "细化研究方法",
    "STUDY_DESIGN": "设计研究方案",
    "ANALYSIS_PLANNING": "设计分析方案",
    "FEASIBILITY_REVIEW": "独立审查与修订",
    "HUMAN_REVISION_REVIEW": "人工审查修订建议",
    "REVISION": "执行已批准的修订计划",
    "CRITICAL_REVIEW": "修订后独立复审",
    "HUMAN_APPROVAL": "等待人工批准",
    "SYNTHESIS": "形成研究方案",
    "COMPLETED": "研究方案完成",
    "EXECUTION_WAITING": "等待实验执行",
    "EXECUTION": "正在执行实验",
    "DATA_ANALYSIS": "分析实验结果",
    "HUMAN_INTERVENTION_REQUIRED": "需要人工处理",
    "FAILED": "执行失败",
    "CANCELLED": "项目已取消",
}

STATUS_LABELS = {
    "success": "执行成功",
    "complete": "已完成",
    "completed": "已完成",
    "failed": "执行失败",
    "rejected": "已拒绝",
    "pending": "等待处理",
    "running": "正在运行",
    "queued": "等待运行",
    "approve": "通过",
    "revise": "需要修订",
    "reject": "不通过",
    "supported": "有证据支持",
    "partially_supported": "部分支持",
    "unsupported": "证据不足",
    "contradicted": "存在反证",
    "unknown": "尚未判断",
}

EXECUTION_CAPABILITY_VIEWS = {
    "INTERNAL_EXECUTABLE": {
        "label": "系统可直接执行数值实验",
        "description": "当前研究已绑定经过批准的确定性执行器；只有点击“运行下一阶段”后才会开始执行。",
        "action": "运行下一阶段，系统将保存真实执行结果和反馈记录。",
    },
    "EXTERNAL_EXECUTION_REQUIRED": {
        "label": "需要研究者完成外部实验",
        "description": "当前问题没有可安全自动运行的执行器，系统不会伪造实验、仪器或分析结果。",
        "action": "请在外部完成实验后上传 CSV、Excel、JSON、文本或 PDF 结果，再运行下一阶段。",
    },
    "PLANNING_ONLY": {
        "label": "仅生成研究方案",
        "description": "本项目只生成经过审查的研究方案，不执行实验或数据分析。",
        "action": "完成方案审查后，可下载研究包供后续人工执行。",
    },
}


def status_label(value: Any) -> str:
    """Translate known machine statuses while preserving unknown values."""

    text = "" if value is None else str(value)
    return STATUS_LABELS.get(text, PHASE_LABELS.get(text, text))


def execution_capability_view(value: Any) -> dict[str, str]:
    key = "" if value is None else str(value)
    return dict(
        EXECUTION_CAPABILITY_VIEWS.get(
            key,
            {"label": key or "尚未判断", "description": "执行能力尚未确定。", "action": "请继续完成研究规划。"},
        )
    )


def determine_output_language(text: str, explicit: str | None = None) -> str:
    """Choose a response language without changing any research schema."""

    normalized = (explicit or "auto").strip().lower()
    if normalized in {"en", "english", "en-us", "en-gb"}:
        return "en"
    if normalized in {"zh", "zh-cn", "chinese", "中文"}:
        return "zh-CN"
    content = text or ""
    if re.search(r"\b(?:respond|answer|write|output|reply)\s+in\s+english\b", content, re.I) or "请用英文" in content:
        return "en"
    if re.search(r"[\u4e00-\u9fff]", content):
        return "zh-CN"
    return "auto"


def language_instruction(language: str) -> str:
    if language == "zh-CN":
        return "所有面向用户的自然语言字段使用简体中文；字段名和引用 ID 保持结构化 schema 要求。"
    if language == "en":
        return "Write all user-facing natural-language fields in English; preserve schema field names and reference IDs."
    return "Use the language of the user's research question for user-facing natural-language fields."


def research_mode_label(value: Any) -> str:
    return _mode_label(value) if value else "尚未选择"


def domain_label(value: Any) -> str:
    return _domain_label(str(value)) if value else "通用研究"


def feedback_signal_view(signal: dict[str, Any]) -> dict[str, Any]:
    observed = signal.get("observed_result") or signal.get("observed_metrics") or signal.get("metrics") or {}
    flags = signal.get("quality_flags") or signal.get("flags") or signal.get("diagnostic_flags") or []
    flag_labels = {
        "boundary_optimum": "最优值位于搜索边界",
        "coarse_resolution": "搜索分辨率偏粗",
        "insufficient_improvement": "改善幅度不足",
        "high_error": "拟合误差仍较高",
        "rmse_above_success_threshold": "RMSE 尚未达到成功阈值",
    }
    trigger_labels = {
        "round_1_deterministic_fit_evaluation": "第一轮确定性拟合未达到预设成功阈值",
    }
    raw_trigger = signal.get("trigger") or signal.get("reason")
    return {
        "status": status_label(signal.get("status")),
        "round": signal.get("round") or signal.get("research_round"),
        "rmse": observed.get("rmse", signal.get("rmse")),
        "flags": [flag_labels.get(str(item), str(item)) for item in flags],
        "trigger": trigger_labels.get(str(raw_trigger), str(raw_trigger)) if raw_trigger else "根据本轮真实执行结果生成反馈。",
        "evidence_refs": list(signal.get("evidence_refs") or signal.get("source_artifact_ids") or []),
    }


def plan_adjustment_rows(adjustment: dict[str, Any]) -> list[dict[str, Any]]:
    old_value = adjustment.get("old_value") or {}
    new_value = adjustment.get("new_value") or {}
    fields = list(dict.fromkeys([*old_value.keys(), *new_value.keys()]))
    return [
        {
            "调整项": field,
            "调整前": old_value.get(field),
            "调整后": new_value.get(field),
            "原因": adjustment.get("reason") or "根据本轮反馈调整",
            "证据引用": "、".join(adjustment.get("evidence_refs") or []),
        }
        for field in fields
    ]


def execution_result_view(result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("metrics") or result.get("evaluation") or {}
    parameters = result.get("best_parameters") or result.get("parameters") or {}
    artifacts = result.get("artifacts") or result.get("artifact_refs") or []
    return {
        "status": status_label(result.get("status")),
        "executor": result.get("executor_name") or result.get("executor_id") or "确定性白名单执行器",
        "seed": result.get("reproducibility_seed", result.get("seed")),
        "evaluations": result.get("evaluation_count", metrics.get("evaluations")),
        "rmse": metrics.get("rmse", result.get("rmse")),
        "best_parameters": parameters,
        "duration_ms": result.get("duration_ms", result.get("latency_ms")),
        "artifacts": [
            (item.get("filename") or item.get("relative_path") or item.get("artifact_type"))
            if isinstance(item, dict)
            else str(item)
            for item in artifacts
        ],
    }


def user_error_message(error: Any) -> str:
    """Return a safe, concise user-facing message; diagnostics stay in developer details."""

    detail = getattr(error, "detail", error)
    if isinstance(detail, list):
        messages = [str(item.get("msg") or "输入内容不符合要求") for item in detail if isinstance(item, dict)]
        return "提交内容有误：" + "；".join(messages[:3])
    if isinstance(detail, dict):
        detail = detail.get("error_message") or detail.get("message") or detail.get("detail")
    text = str(detail or "操作未完成，请稍后重试。")
    if "Traceback" in text:
        return "操作未完成。技术详情已隐藏，可在开发者调试模式中查看。"
    return text[:500]


def model_service_error_message(error: Any) -> str:
    """Map provider failures to a stable recovery message without leaking details."""

    detail = getattr(error, "detail", error)
    if isinstance(detail, dict):
        error_type = str(detail.get("error_type") or detail.get("type") or "")
        message = str(detail.get("error_message") or detail.get("message") or "")
        status_code = detail.get("status_code")
    else:
        error_type, message, status_code = type(error).__name__, str(detail or ""), None
    fingerprint = f"{error_type} {message}".lower()
    if "timeout" in fingerprint:
        return "模型服务本次响应超时。项目数据没有丢失，可以直接重试当前阶段。"
    if any(marker in fingerprint for marker in ("apiconnectionerror", "connection", "network")):
        return "模型服务连接暂时失败。项目数据没有丢失，可以稍后重试当前阶段。"
    if status_code == 429 or any(marker in fingerprint for marker in ("ratelimiterror", "rate limit", "429")):
        return "模型服务当前繁忙或达到速率限制，请稍后重试。"
    return "模型服务暂时未能完成本阶段。项目数据没有丢失，可以稍后重试当前阶段。"


def safe_error_debug_details(error: dict[str, Any]) -> dict[str, Any]:
    """Keep useful failure audit fields while redacting secrets and absolute paths."""

    allowed = (
        "error_type", "error_message", "stage", "stage_substep",
        "failing_component", "failure_category", "cause_type",
    )
    result: dict[str, Any] = {}
    for key in allowed:
        value = error.get(key)
        if value in (None, ""):
            continue
        text = str(value)
        text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;}]+", r"\1[REDACTED]", text)
        text = re.sub(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;}]+", r"\1[REDACTED]", text)
        text = re.sub(r"(?:[A-Za-z]:\\|/)(?:[^\s:]+[/\\])+[^\s:]*", "[REDACTED_PATH]", text)
        result[key] = text[:1000]
    return result


def research_step_action_state(
    project_phase: str,
    job_status: str | None,
    *,
    human_gate: bool = False,
) -> dict[str, Any]:
    """Return the explicit UI action for a project's current job state."""

    terminal_labels = {
        "COMPLETED": "✅ 研究流程已完成",
        "FAILED": "研究项目已失败",
        "CANCELLED": "研究项目已取消",
    }
    if project_phase in terminal_labels:
        return {"label": terminal_labels[project_phase], "disabled": True}
    if job_status in {"queued", "running"}:
        return {"label": "⏳ 当前阶段正在运行", "disabled": True}
    if job_status == "failed":
        return {"label": "🔄 重试本阶段", "disabled": human_gate}
    return {"label": "▶ 运行下一阶段", "disabled": human_gate}


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
    if event.visibility != "user":
        return ""
    time_text = format_local_time(event.started_at)
    phase = PHASE_LABELS.get(event.phase.value, event.phase.value)
    if event.status == "running" and event.display_key:
        return f"{time_text}　{phase}开始。"
    if event.status == "failed":
        detail = event.error_message or event.error or "阶段执行失败，项目保留在上一完整阶段。"
        return f"{time_text}　{phase}失败：{detail}"
    if event.display_markdown:
        return f"{time_text}　{event.display_markdown}"
    if event.summary_markdown:
        return f"{time_text}　{event.summary_markdown}"
    return ""


def render_event_dict(event: dict[str, Any]) -> str:
    if event.get("visibility") != "user":
        return ""
    phase_value = str(event.get("phase", ""))
    time_text = format_local_time(event.get("started_at"))
    phase = PHASE_LABELS.get(phase_value, phase_value)
    status = event.get("status")
    if status == "running" and event.get("display_key"):
        return f"{time_text}　{phase}开始。"
    if status == "failed":
        detail = event.get("display_markdown") or event.get("error_message") or "阶段执行失败，项目保留在上一完整阶段。"
        return f"{time_text}　{detail}"
    if event.get("summary_markdown"):
        return f"{time_text}　{event['summary_markdown']}"
    if event.get("display_markdown"):
        return f"{time_text}　{event['display_markdown']}"
    return ""


def dedupe_user_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return stable user events once, including legacy logs with duplicates."""

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for event in events:
        if event.get("visibility") != "user":
            continue
        key = str(event.get("display_key") or "").strip()
        if not key:
            continue
        dedupe_key = f"{event.get('phase')}:{event.get('iteration', 0)}:{key}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        result.append(event)
    return result


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
