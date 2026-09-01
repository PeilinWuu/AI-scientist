"""Unified Streamlit frontend for AI Scientist."""

from __future__ import annotations

import json
import base64
import logging
import os
import time
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv

from src.ai_scientist.presentation import (
    PHASE_LABELS,
    dedupe_user_events,
    domain_label,
    execution_capability_view,
    execution_result_view,
    feedback_signal_view,
    plan_adjustment_rows,
    research_mode_label,
    render_event_dict,
    render_project_overview,
    status_label,
    user_error_message,
)
from src.ui_time import format_local_datetime, format_utc_datetime
from src.model_utils import normalize_model_name


ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

DEFAULT_BACKEND_URL = "http://localhost:8000"
RESEARCH_STEP_TIMEOUT = int(os.getenv("AI_SCIENTIST_FRONTEND_STEP_TIMEOUT", "600"))
RESEARCH_ASSET_EXTENSIONS = ["pdf", "md", "txt", "csv", "tsv", "json", "xml", "xlsx", "xls"]
RESEARCH_ASSET_MAX_BYTES = int(os.getenv("AI_SCIENTIST_MAX_ASSET_BYTES", str(25 * 1024 * 1024)))
LOGGER = logging.getLogger(__name__)

DAMPED_OSCILLATOR_EXAMPLE = {
    "objective": (
        "研究如何从带噪位移观测中辨识阻尼振子的阻尼系数与角频率，并根据第一轮拟合结果"
        "调整第二轮参数搜索范围，在受控计算预算内降低拟合 RMSE。"
    ),
    "domain_hint": "physics",
    "constraints_text": (
        "使用有界参数搜索；Round 1 采用宽范围粗网格，Round 2 必须引用第一轮实际结果再细化；"
        "禁止执行任意 LLM 生成代码。建议初始范围：damping=[0.05, 0.35]，omega=[2.0, 2.8]。"
    ),
    "seed": 20260831,
    "case_id": "competition_1b_damped_oscillator",
}

SCIENTIST_MODEL_KEYS = {
    "research_director": ("scientist_director_model", "研究总监模型"),
    "evidence_researcher": ("scientist_research_model", "证据研究员模型"),
    "methodologist": ("scientist_methodologist_model", "方法学专家模型"),
    "hypothesis_scientist": ("scientist_hypothesis_model", "假设科学家模型"),
    "study_designer": ("scientist_designer_model", "研究设计师模型"),
    "analyst": ("scientist_analyst_model", "分析师模型"),
    "reproducibility_engineer": ("scientist_reproducibility_model", "可复现性工程师模型"),
    "skeptical_reviewer": ("scientist_reviewer_model", "独立审查员模型"),
    "revision_verifier": ("scientist_revision_verifier_model", "修订验证模型"),
    "scientific_synthesizer": ("scientist_synthesizer_model", "科学综合模型"),
    "fallback": ("scientist_fallback_model", "备用模型"),
}


class BackendAPIError(RuntimeError):
    """Error wrapper that preserves safe backend JSON detail."""

    def __init__(self, status_code: int, detail: object) -> None:
        super().__init__(f"Backend API error {status_code}")
        self.status_code = status_code
        self.detail = detail


def _extract_error_detail(response: requests.Response) -> object:
    try:
        payload = response.json()
    except ValueError:
        return {"error_message": response.text}
    return payload.get("detail", payload)


def post_json(
    backend_url: str,
    path: str,
    payload: dict | None = None,
    timeout: int = 120,
) -> dict:
    url = f"{backend_url.rstrip('/')}{path}"
    response = requests.post(url, json=payload, timeout=timeout)
    if not response.ok:
        raise BackendAPIError(response.status_code, _extract_error_detail(response))
    return response.json()


def delete_json(backend_url: str, path: str, timeout: int = 60) -> dict:
    response = requests.delete(f"{backend_url.rstrip('/')}{path}", timeout=timeout)
    if not response.ok:
        raise BackendAPIError(response.status_code, _extract_error_detail(response))
    return response.json()


def get_json(backend_url: str, path: str) -> object:
    url = f"{backend_url.rstrip('/')}{path}"
    response = requests.get(url, timeout=60)
    if not response.ok:
        raise BackendAPIError(response.status_code, _extract_error_detail(response))
    return response.json()


def get_text(backend_url: str, path: str) -> str:
    url = f"{backend_url.rstrip('/')}{path}"
    response = requests.get(url, timeout=60)
    if not response.ok:
        raise BackendAPIError(response.status_code, _extract_error_detail(response))
    return response.text


def render_artifact_image(image_url: str, caption: str = "") -> None:
    """Render an artifact with the Streamlit 1.37 image API and a safe failure state."""

    try:
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()
        st.image(response.content, caption=caption or None, use_column_width=True)
    except Exception as exc:  # noqa: BLE001 - preview failure must not break the product page
        LOGGER.warning("Artifact preview unavailable for %s: %s", image_url, exc)
        st.warning("暂时无法预览该产物，可下载文件后查看。")


def _save_research_assets(
    backend_url: str,
    project_id: str,
    uploads: list,
    purpose: str,
    description: str,
    upload_context: str,
    *,
    asset_role: str = "research_material",
    research_round: int | None = None,
    source: str = "user_upload",
) -> tuple[list[dict], list[str]]:
    """Persist uploaded files through the project asset API and report partial failures."""

    saved: list[dict] = []
    errors: list[str] = []
    for upload in uploads:
        content = upload.getvalue()
        if len(content) > RESEARCH_ASSET_MAX_BYTES:
            errors.append(
                f"{upload.name}：超过 {RESEARCH_ASSET_MAX_BYTES // (1024 * 1024)} MB 限制。"
            )
            continue
        try:
            result = post_json(
                backend_url,
                f"/api/research/{project_id}/research-assets",
                {
                    "filename": upload.name,
                    "content_type": upload.type or "application/octet-stream",
                    "content_base64": base64.b64encode(content).decode("ascii"),
                    "purpose": purpose,
                    "asset_role": asset_role,
                    "research_round": research_round,
                    "source": source,
                    "description": description.strip(),
                    "upload_context": upload_context,
                },
                timeout=120,
            )
            saved.append(result.get("asset") or {})
        except BackendAPIError as exc:
            detail = exc.detail
            message = detail.get("error_message") if isinstance(detail, dict) else str(detail)
            errors.append(f"{upload.name}：{message or detail}")
    return saved, errors


def _render_research_asset_uploader(
    backend_url: str,
    project: dict,
    *,
    upload_context: str,
    key_prefix: str,
    title: str = "上传补充资料或数据",
    expanded: bool = False,
    embedded: bool = False,
    allow_experimental_results: bool = False,
) -> None:
    """Render one auditable multi-file upload entry for an existing project."""

    project_id = str(project.get("project_id") or "")
    container = st.container(border=True) if embedded else st.expander(title, expanded=expanded)
    with container:
        if embedded:
            st.markdown(f"**{title}**")
        st.caption(
            "支持 PDF、Markdown、TXT、CSV、TSV、JSON、XML 和 Excel。"
            "文件会在本地安全解析为有边界的文本或结构摘要，并在后续科研阶段作为带来源标识的材料使用。"
        )
        uploads = st.file_uploader(
            "选择一个或多个文件",
            type=RESEARCH_ASSET_EXTENSIONS,
            accept_multiple_files=True,
            key=f"{key_prefix}_files_{project_id}",
        )
        asset_role = "research_material"
        research_round = None
        source = "user_upload"
        if allow_experimental_results:
            asset_role = st.radio(
                "添加类型",
                options=["research_material", "experimental_result"],
                format_func=lambda value: {
                    "research_material": "添加研究资料",
                    "experimental_result": "添加实验结果",
                }[value],
                horizontal=True,
                key=f"{key_prefix}_role_{project_id}",
            )
        if asset_role == "experimental_result":
            purpose = "data"
            research_round = int(st.number_input(
                "实验轮次",
                min_value=1,
                value=max(1, int(project.get("iteration") or 0) + 1),
                key=f"{key_prefix}_round_{project_id}",
            ))
            source = st.text_input(
                "数据来源",
                value="human_experiment",
                key=f"{key_prefix}_source_{project_id}",
                help="例如：human_experiment、instrument_export 或 external_lab。",
            )
        else:
            purpose = st.radio(
                "文件用途",
                options=["reference", "data", "other"],
                format_func=lambda value: {
                    "reference": "参考资料",
                    "data": "数据来源",
                    "other": "其他项目文件",
                }[value],
                horizontal=True,
                key=f"{key_prefix}_purpose_{project_id}",
            )
        description = st.text_area(
            "文件说明（可选）",
            placeholder="例如：研究者提供的原始数据、需要复核的方法附件或补充文献。",
            key=f"{key_prefix}_description_{project_id}",
        )
        if st.button(
            "保存到项目",
            disabled=not uploads,
            key=f"{key_prefix}_save_{project_id}",
        ):
            saved, errors = _save_research_assets(
                backend_url,
                project_id,
                list(uploads or []),
                purpose,
                description,
                "experimental_result" if asset_role == "experimental_result" else upload_context,
                asset_role=asset_role,
                research_round=research_round,
                source=source,
            )
            if saved:
                st.success(f"已保存 {len(saved)} 个文件。")
                if asset_role == "experimental_result":
                    try:
                        post_json(
                            backend_url,
                            f"/api/research/{project_id}/provide-data",
                            {
                                "artifact_paths": [str(item.get("saved_path") or item.get("asset_id")) for item in saved],
                                "description": description or f"Round {research_round} experimental result",
                                "data_type": ",".join(sorted({str(item.get("content_type") or "data") for item in saved})),
                            },
                        )
                    except BackendAPIError as exc:
                        st.warning("文件已保存，但当前工作流阶段尚不能登记为待分析实验结果。")
                        LOGGER.warning("Experimental result registration deferred: %s", exc.detail)
                refresh_research_project(backend_url)
            for error in errors:
                st.error(error)

        assets = project.get("research_assets") or []
        if assets:
            st.markdown("**当前项目已登记文件**")
            for item in assets:
                purpose_label = {
                    "reference": "参考资料",
                    "data": "数据来源",
                    "other": "其他",
                }.get(item.get("purpose"), "参考资料")
                parsing_status = str(item.get("parsing_status") or "registered_only")
                status_label = {
                    "registered_only": "已登记，等待解析",
                    "parsing": "正在解析",
                    "parsed": "已解析",
                    "failed": "解析失败，可重试",
                }.get(parsing_status, parsing_status)
                filename = str(item.get("filename") or "未命名文件")
                asset_url = (
                    f"{backend_url.rstrip('/')}/api/research/{project_id}/research-assets/"
                    f"{item.get('asset_id')}"
                )
                details = st.columns([3, 2])
                details[0].link_button(filename, asset_url, use_container_width=True)
                details[1].caption(
                    f"{purpose_label} · {round(float(item.get('size_bytes') or 0) / 1024, 1)} KB · "
                    f"{status_label}"
                )
                if item.get("asset_role") == "experimental_result":
                    st.caption(
                        f"实验结果 · Round {item.get('research_round') or '未指定'} · "
                        f"来源：{item.get('source') or 'user_upload'} · asset id: {item.get('asset_id')}"
                    )
                else:
                    st.caption(f"asset id: {item.get('asset_id')}")
                if item.get("description"):
                    st.caption(str(item.get("description")))
                parsed = item.get("parsed_content") or {}
                if parsed:
                    structured = parsed.get("structured_summary") or {}
                    columns = structured.get("column_names") or []
                    rows = structured.get("scanned_data_rows")
                    if columns or rows is not None:
                        missing = structured.get("missing_values_in_scanned_rows") or {}
                        missing_total = sum(value for value in missing.values() if isinstance(value, int))
                        st.caption(
                            f"解析完成：读取 {rows if rows is not None else '未知'} 行、{len(columns)} 列"
                            f"（{ '、'.join(columns) or '未识别列名' }），扫描范围内缺失值 {missing_total} 个。"
                        )
                    elif structured.get("root_type"):
                        keys = structured.get("top_level_keys") or []
                        st.caption(
                            f"解析完成：JSON 顶层类型为 {structured.get('root_type')}，"
                            f"包含 {structured.get('top_level_key_count', len(keys))} 个顶层字段"
                            f"（{'、'.join(keys[:8]) or '未识别字段'}{'…' if len(keys) > 8 else ''}）。"
                        )
                    else:
                        st.caption(f"解析器：{parsed.get('parser_name')} · {parsed.get('summary')}")
                    warnings = parsed.get("warnings") or []
                    if warnings:
                        st.caption("解析提示：" + "；".join(str(value) for value in warnings))
                    used_by = item.get("used_by_agents") or []
                    st.caption(
                        "后续使用记录："
                        + ("、".join(str(value) for value in used_by) if used_by else "尚未进入使用该材料的研究阶段")
                    )
                if parsing_status in {"registered_only", "failed"}:
                    if item.get("parse_error"):
                        st.caption(f"失败原因：{item.get('parse_error')}")
                    if st.button(
                        "解析文件" if parsing_status == "registered_only" else "重新解析",
                        key=f"{key_prefix}_parse_{project_id}_{item.get('asset_id')}",
                    ):
                        try:
                            post_json(
                                backend_url,
                                f"/api/research/{project_id}/research-assets/{item.get('asset_id')}/parse",
                                {},
                                timeout=120,
                            )
                            refresh_research_project(backend_url)
                            st.rerun()
                        except BackendAPIError as exc:
                            st.error(user_error_message(exc))
                delete_key = f"{key_prefix}_delete_confirm_{project_id}_{item.get('asset_id')}"
                if not item.get("used_by_agents"):
                    allow_delete = st.checkbox("允许删除此误上传文件", key=delete_key)
                    if st.button(
                        "删除文件",
                        disabled=not allow_delete,
                        key=f"{key_prefix}_delete_{project_id}_{item.get('asset_id')}",
                    ):
                        try:
                            delete_json(
                                backend_url,
                                f"/api/research/{project_id}/research-assets/{item.get('asset_id')}",
                            )
                            refresh_research_project(backend_url)
                            st.rerun()
                        except BackendAPIError as exc:
                            st.error(user_error_message(exc))


def get_model_config(backend_url: str) -> dict:
    try:
        config = get_json(backend_url, "/api/config/models")
        return config if isinstance(config, dict) else {}
    except Exception:
        return {}


def model_default(config: dict, section: str, fallback: str) -> str:
    value = (config.get(section) or {}).get("default_model")
    return str(value or fallback)


def effective_model_input(key: str, default_model: str) -> str | None:
    try:
        return normalize_model_name(st.session_state.get(key)) or default_model
    except ValueError as exc:
        st.sidebar.error(str(exc))
        return None


def scientist_default_models(config: dict) -> dict[str, str]:
    scientist = config.get("ai_scientist") or {}
    roles = scientist.get("roles") or {}
    fallback = str(scientist.get("fallback_model") or os.getenv("LLM_MODEL", "qwen-turbo"))
    defaults = {role: str(roles.get(role) or fallback) for role in SCIENTIST_MODEL_KEYS if role != "fallback"}
    defaults["fallback"] = fallback
    return defaults


def scientist_model_overrides(defaults: dict[str, str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for role, (key, _) in SCIENTIST_MODEL_KEYS.items():
        value = normalize_model_name(st.session_state.get(key))
        if value and value != defaults.get(role):
            overrides[role] = value
    return overrides


def test_model(backend_url: str, model: str, mode: str) -> dict:
    return post_json(backend_url, "/api/models/test", {"model": model, "mode": mode}, timeout=180)


def render_model_test_result(result: dict) -> None:
    if result.get("status") == "ok":
        st.success(f"模型可用：{result.get('model')}（{result.get('latency_ms')} ms）")
    else:
        st.error(f"模型不可用：{result.get('model')}")
        st.caption(str(result.get("message") or result.get("error_category") or "模型测试失败。"))


def render_model_test_table(rows: list[dict]) -> None:
    if not rows:
        return
    st.dataframe(rows, use_container_width=True, hide_index=True)


def normalize_records(records: object) -> list[dict]:
    """Convert API records into plain rows that Streamlit can render safely."""

    normalized: list[dict] = []
    for item in records or []:
        if hasattr(item, "model_dump"):
            normalized.append(item.model_dump(mode="json"))
        elif isinstance(item, dict):
            normalized.append(item)
        else:
            normalized.append({"value": str(item)})
    return normalized


def render_debug_object(data: object) -> None:
    """Render diagnostic objects without dumping raw JSON."""

    if isinstance(data, dict):
        rows = [{"field": str(key), "value": _short_debug_value(value)} for key, value in data.items()]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    elif isinstance(data, list):
        rows = normalize_records(data)
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("没有调试信息。")
    else:
        st.write(_short_debug_value(data))


def _short_debug_value(value: object) -> str:
    if isinstance(value, (dict, list)):
        return f"{type(value).__name__} with {len(value)} item(s)"
    return "" if value is None else str(value)


def _render_execution_round(title: str, result: dict) -> None:
    view = execution_result_view(result)
    st.markdown(f"### {title}")
    columns = st.columns(5)
    columns[0].metric("状态", view["status"] or "未知")
    columns[1].metric("RMSE", f"{view['rmse']:.6f}" if isinstance(view["rmse"], (int, float)) else "—")
    columns[2].metric("参数组合数", view["evaluations"] if view["evaluations"] is not None else "—")
    columns[3].metric("随机种子", view["seed"] if view["seed"] is not None else "自动")
    columns[4].metric("运行耗时", f"{view['duration_ms']} ms" if view["duration_ms"] is not None else "—")
    metrics = result.get("metrics") or {}
    if metrics:
        st.markdown(
            f"**最佳参数：** 阻尼系数 {metrics.get('best_damping', '—')}；"
            f"角频率 {metrics.get('best_omega', '—')}"
        )
    st.caption(f"执行器：{view['executor']}")
    if view["artifacts"]:
        st.markdown("**生成产物：** " + "；".join(view["artifacts"]))


def _render_final_synthesis(conclusion: dict) -> None:
    statement = conclusion.get("planning_status_statement")
    if statement:
        st.info(statement)
    section_specs = [
        ("有证据支持的发现", "supported_findings"),
        ("暂定推论", "tentative_inferences"),
        ("证据不足的主张", "unsupported_claims"),
        ("阴性结果", "negative_results"),
        ("不确定性", "uncertainties"),
        ("适用范围", "scope_of_validity"),
        ("研究局限", "limitations"),
        ("后续问题", "next_questions"),
    ]
    for title, key in section_specs:
        items = conclusion.get(key) or []
        st.markdown(f"### {title}")
        if not items:
            st.caption("暂无内容。")
            continue
        for item in items:
            if isinstance(item, dict):
                statement = item.get("statement") or item.get("finding") or "未命名条目"
                st.markdown(f"- {statement}")
                refs = item.get("supporting_evidence_ids") or item.get("evidence_refs") or []
                if refs:
                    st.caption("  证据引用：" + "、".join(refs))
                if item.get("confidence") is not None:
                    st.caption(f"  置信度：{item.get('confidence')}")
                if item.get("limitations"):
                    st.caption("  局限：" + "；".join(item.get("limitations") or []))
            else:
                st.markdown(f"- {item}")
    if conclusion.get("human_verification_required"):
        st.warning("该研究方案仍需人工核验后才能用于真实决策或实验执行。")


def refresh_research_project(backend_url: str) -> dict | None:
    project_id = st.session_state.research_project_id
    if not project_id:
        return None
    project = get_json(backend_url, f"/api/research/{project_id}")
    st.session_state.research_project = project
    return project  # type: ignore[return-value]


def render_scientist_model_config(backend_url: str) -> dict[str, str]:
    config = get_model_config(backend_url)
    defaults = scientist_default_models(config)
    for role, (key, _) in SCIENTIST_MODEL_KEYS.items():
        if key not in st.session_state:
            st.session_state[key] = defaults.get(role, "")

    with st.expander("模型团队配置", expanded=False):
        if st.button("从 .env 重置模型团队"):
            for role, (key, _) in SCIENTIST_MODEL_KEYS.items():
                st.session_state[key] = defaults.get(role, "")
            st.rerun()
        with st.form("scientist_model_team_form"):
            for role, (key, label) in SCIENTIST_MODEL_KEYS.items():
                st.text_input(
                    label,
                    key=key,
                    placeholder="输入 DashScope 模型 ID；留空使用服务器默认值",
                )
            apply_models = st.form_submit_button("应用模型配置")
            test_models = st.form_submit_button("测试模型团队")
        if apply_models:
            st.success("模型配置已在当前前端会话中生效，并会保存到新建项目。")
        if test_models:
            rows = []
            model_to_roles: dict[tuple[str, str], list[str]] = {}
            for role, (key, label) in SCIENTIST_MODEL_KEYS.items():
                model = normalize_model_name(st.session_state.get(key)) or defaults.get(role, "")
                if not model:
                    continue
                mode = "search" if role == "evidence_researcher" else "chat"
                model_to_roles.setdefault((model, mode), []).append(label)
            for (model, test_mode), labels in model_to_roles.items():
                result = test_model(backend_url, model, test_mode)
                rows.append(
                    {
                        "模型名称": model,
                        "用途": "、".join(labels),
                        "状态": "可用" if result.get("status") == "ok" else "不可用",
                        "说明": "调用成功" if result.get("status") == "ok" else result.get("message", "调用失败"),
                    }
                )
            render_model_test_table(rows)
        st.caption("这些输入仅影响当前前端会话和新建项目，不会修改 .env 文件。")
    return defaults


def _load_damped_oscillator_example() -> None:
    """Populate editable intake fields without creating or running anything."""

    current_question = str(st.session_state.get("research_objective") or "").strip()
    if current_question and current_question != DAMPED_OSCILLATOR_EXAMPLE["objective"]:
        st.session_state.research_example_notice = "preserved_user_input"
        return
    st.session_state.research_objective = DAMPED_OSCILLATOR_EXAMPLE["objective"]
    st.session_state.research_domain_hint = DAMPED_OSCILLATOR_EXAMPLE["domain_hint"]
    st.session_state.research_constraints_text = DAMPED_OSCILLATOR_EXAMPLE["constraints_text"]
    st.session_state.research_seed_mode = "Custom"
    st.session_state.research_custom_seed = DAMPED_OSCILLATOR_EXAMPLE["seed"]
    st.session_state.research_planning_only = False
    st.session_state.research_example_case = DAMPED_OSCILLATOR_EXAMPLE["case_id"]
    st.session_state.research_example_notice = "loaded"


def _clear_example_marker() -> None:
    """Detach benchmark behavior while preserving user-editable text."""

    st.session_state.research_example_case = ""
    st.session_state.research_example_notice = "detached"


def _on_research_question_change() -> None:
    """Keep an explicitly loaded executor binding while allowing question edits."""

    if (
        st.session_state.get("research_example_case")
        and st.session_state.get("research_objective") != DAMPED_OSCILLATOR_EXAMPLE["objective"]
    ):
        st.session_state.research_example_notice = "edited"


def build_research_start_payload(
    *,
    objective: str,
    domain_hint: str,
    constraints_text: str,
    model_overrides: dict[str, str],
    max_iterations: int,
    planning_only: bool,
    evidence_review_mode: str,
    seed_mode: str,
    custom_seed: int,
    example_case: str = "",
) -> dict:
    """Build project intake without creating a project or starting a workflow stage."""

    question = objective.strip()
    if not question:
        raise ValueError("请输入科学问题后再创建研究项目。")
    seed = int(custom_seed) if seed_mode == "Custom" else None
    constraints: dict[str, object] = {"reproducibility_seed_mode": seed_mode.lower()}
    if example_case:
        constraints["example_case"] = example_case
    return {
        "objective": question,
        "domain_hint": domain_hint.strip() or None,
        "constraints_text": constraints_text.strip(),
        "constraints": constraints,
        "model_overrides": model_overrides,
        "max_iterations": int(max_iterations),
        "planning_only": bool(planning_only),
        "evidence_review_mode": evidence_review_mode,
        "reproducibility_seed": seed,
    }


def render_research_workspace(backend_url: str, show_debug: bool) -> None:
    st.title("AI Scientist")
    st.caption("输入科学问题和已有资料，系统将形成可审计的实验规划、执行、分析与反馈迭代流程。")
    model_defaults = scientist_default_models(get_model_config(backend_url))

    if not st.session_state.research_project_id:
        st.subheader("你想研究什么？")
        example_columns = st.columns([3, 1])
        example_columns[0].caption(
            "从自己的问题开始；Competition 1B 阻尼振子只是可编辑且不会自动运行的示例。"
        )
        example_columns[1].button(
            "加载示例：阻尼振子参数辨识",
            on_click=_load_damped_oscillator_example,
            use_container_width=True,
        )
        if st.session_state.research_example_notice == "preserved_user_input":
            st.warning("未加载示例：已保留你输入的科学问题。请先清空问题，再主动加载示例。")
        elif st.session_state.research_example_case:
            st.info(
                "示例问题、参数约束、观测数据和推荐 seed 已准备；所有内容仍可编辑，"
                "内部数值模拟尚未运行。"
            )
            st.button("移除示例标记（保留文本）", on_click=_clear_example_marker)
        elif st.session_state.research_example_notice == "detached":
            st.info("已保留编辑后的内容，并解除 Competition 示例标记；不会自动运行 benchmark。")

    intake_container = (
        st.container(border=True)
        if not st.session_state.research_project_id
        else st.expander("新建或加载其他研究项目", expanded=False)
    )
    with intake_container:
        existing_project_id = st.text_input("加载已有项目 ID", key="existing_research_project_id")
        if st.button("加载项目"):
            try:
                st.session_state.research_project_id = existing_project_id.strip()
                refresh_research_project(backend_url)
                st.rerun()
            except BackendAPIError as exc:
                st.error(user_error_message(exc))
        objective = st.text_area(
            "科学问题",
            key="research_objective",
            height=180,
            placeholder="请描述你希望研究的科学问题、目标、已有条件和限制。",
            on_change=_on_research_question_change,
        )
        domain_hint = st.text_input("领域提示（可选）", key="research_domain_hint")
        constraints_text = st.text_area(
            "补充要求与约束",
            value="",
            key="research_constraints_text",
            placeholder="例如：目标人群、可用数据、时间范围、约束条件，以及是否允许因果推断。",
        )
        advanced_settings_container = (
            st.expander("高级设置", expanded=False)
            if not st.session_state.research_project_id
            else st.container(border=True)
        )
        with advanced_settings_container:
            if st.session_state.research_project_id:
                st.caption("高级设置")
            seed_mode = st.radio(
                "可复现随机种子",
                options=["Auto", "Custom"],
                format_func=lambda value: {"Auto": "自动", "Custom": "自定义"}[value],
                horizontal=True,
                key="research_seed_mode",
                help="seed 只控制可复现随机性，不会选择问题、加载案例或启动执行。",
            )
            custom_seed = st.number_input(
                "自定义随机种子",
                min_value=0,
                max_value=2_147_483_647,
                step=1,
                key="research_custom_seed",
                disabled=seed_mode != "Custom",
            )
            if st.session_state.research_example_case:
                st.caption("比赛示例可复现随机种子：20260831（可修改）")
            max_iterations = st.number_input("最大修订次数", min_value=0, max_value=10, value=2)
            planning_only = st.checkbox(
                "仅生成研究方案（不等待实验结果）",
                value=False,
                key="research_planning_only",
                help="未选中时，如无内部执行器，项目会等待研究者完成外部实验并上传真实结果。",
            )
            evidence_review_mode = st.selectbox(
                "证据审查模式",
                options=["ASSISTED", "MANUAL", "AUTO"],
                format_func=lambda value: {
                    "ASSISTED": "辅助审查（推荐）：AI 推荐，人工决定",
                    "MANUAL": "人工审查：所有候选来源均由人工明确决定",
                    "AUTO": "自动审查：用于低风险开发验证",
                }[value],
                index=0,
            )
        creation_uploads = st.file_uploader(
            "项目初始资料或数据（可选）",
            type=RESEARCH_ASSET_EXTENSIONS,
            accept_multiple_files=True,
            key="research_creation_assets",
            help="支持 PDF、文本、结构化数据和 Excel；创建项目后会保存到该项目。",
        )
        creation_asset_purpose = st.radio(
            "初始文件用途",
            options=["reference", "data", "other"],
            format_func=lambda value: {
                "reference": "参考资料",
                "data": "数据来源",
                "other": "其他项目文件",
            }[value],
            horizontal=True,
            key="research_creation_asset_purpose",
        )
        creation_asset_description = st.text_input(
            "初始文件说明（可选）",
            key="research_creation_asset_description",
            placeholder="例如：已有文献、实验记录或待分析数据。",
        )
        st.caption("上传文件会随项目保存并在本地解析；解析摘要会进入后续研究角色的结构化输入。")
        if st.button("创建研究项目", type="primary", disabled=not objective.strip()):
            try:
                payload = build_research_start_payload(
                    objective=objective,
                    domain_hint=domain_hint,
                    constraints_text=constraints_text,
                    model_overrides=scientist_model_overrides(model_defaults),
                    max_iterations=int(max_iterations),
                    planning_only=planning_only,
                    evidence_review_mode=evidence_review_mode,
                    seed_mode=seed_mode,
                    custom_seed=int(custom_seed),
                    example_case=st.session_state.research_example_case,
                )
                created = post_json(
                    backend_url,
                    "/api/research/start",
                    payload,
                )
                st.session_state.research_project_id = created["project_id"]
                saved, upload_errors = _save_research_assets(
                    backend_url,
                    created["project_id"],
                    list(creation_uploads or []),
                    creation_asset_purpose,
                    creation_asset_description,
                    "project_creation",
                )
                refresh_research_project(backend_url)
                st.success(
                    f"项目已创建。{'同时保存了 ' + str(len(saved)) + ' 个文件。' if saved else ''}"
                )
                for upload_error in upload_errors:
                    st.error(upload_error)
            except (ValueError, BackendAPIError) as exc:
                st.error(user_error_message(exc))

    render_scientist_model_config(backend_url)

    project = st.session_state.research_project
    if not project:
        st.info("请先创建项目，再逐阶段运行科研工作流。")
        return

    st.subheader("项目状态")
    columns = st.columns(5)
    phase = project.get("phase", "")
    execution_capability = project.get("execution_capability") or (
        "PLANNING_ONLY" if project.get("planning_only", True) else "EXTERNAL_EXECUTION_REQUIRED"
    )
    phase_label = PHASE_LABELS.get(phase, phase)
    if phase == "EXECUTION_WAITING" and execution_capability == "INTERNAL_EXECUTABLE":
        phase_label = "可以开始项目内执行"
    elif phase == "EXECUTION_WAITING" and execution_capability == "EXTERNAL_EXECUTION_REQUIRED":
        phase_label = "等待外部实验结果"
    elif phase == "HUMAN_INTERVENTION_REQUIRED":
        phase_label = "需要人工处理"
    columns[0].metric("当前阶段", phase_label)
    columns[1].metric("研究模式", research_mode_label(project.get("research_mode")))
    columns[2].metric("研究领域", domain_label(project.get("domain")))
    pending_cycle = 1 if project.get("phase") == "HUMAN_REVISION_REVIEW" and project.get("revision_issues") else 0
    columns[3].metric("修订轮次", project.get("iteration", 0) + pending_cycle)
    budget = project.get("budget") or {}
    columns[4].metric("模型调用", f"{budget.get('used_model_calls', 0)}/{budget.get('max_model_calls', 0)}")
    st.caption(f"项目 ID：{project.get('project_id')}")
    seed_label = project.get("reproducibility_seed")
    st.caption(
        f"工作流版本：{project.get('workflow_version') or 'general_research_v1'} · "
        f"可复现随机种子：{seed_label if seed_label is not None else '自动'}"
    )
    capability_view = execution_capability_view(execution_capability)
    completed_execution = (project.get("internal_execution_summary") or {}).get("status") == "complete"
    completed_python = any(
        item.get("status") == "success" for item in project.get("controlled_python_runs") or []
    )
    if completed_execution or completed_python:
        execution_label = (
            "项目内确定性执行与受控 Python 分析"
            if completed_execution and completed_python
            else "项目内确定性执行"
            if completed_execution
            else "受控 Python 分析"
        )
        st.success(f"执行方式：{execution_label}已完成；真实结果及审计信息已进入复审与最终综合。")
    else:
        st.info(f"执行方式：{capability_view['label']}。{capability_view['description']}")
    if phase == "EXECUTION_WAITING" and execution_capability == "INTERNAL_EXECUTABLE":
        st.success(capability_view["action"])
    elif phase == "EXECUTION_WAITING" and execution_capability == "EXTERNAL_EXECUTION_REQUIRED":
        st.warning(capability_view["action"])
    elif phase == "DATA_ANALYSIS":
        st.info("实验结果已登记。系统只会在已接入真实分析能力时继续，不会伪造分析结果。")
    if project.get("model_overrides"):
        st.caption("该项目使用创建时保存的模型覆盖配置。")
        if show_debug:
            render_debug_object(project.get("model_overrides"))

    _render_research_asset_uploader(
        backend_url,
        project,
        upload_context="project_workspace",
        key_prefix="project_workspace_asset",
        title="项目资料与数据",
        expanded=bool(project.get("research_assets")),
        allow_experimental_results=True,
    )

    execution_summary = project.get("internal_execution_summary") or {}
    if execution_summary:
        st.subheader("确定性执行闭环")
        if execution_summary.get("executor_binding") == "deterministic_data_analysis_v1":
            result_columns = st.columns(4)
            result_columns[0].metric("执行状态", status_label(execution_summary.get("status", "unknown")))
            result_columns[1].metric("数据文件", execution_summary.get("dataset_filename") or "未知")
            result_columns[2].metric("白名单操作", execution_summary.get("operation_count", 0))
            result_columns[3].metric("任意本机代码执行", "禁止")
            st.caption(
                f"输入 SHA-256：{execution_summary.get('dataset_content_sha256') or '未记录'}。"
                "每项操作均保存参数、软件版本、运行时长和输出校验和。"
            )
            operation_rows = [
                {
                    "操作": item.get("operation"),
                    "状态": status_label(item.get("status")),
                    "耗时（毫秒）": item.get("duration_ms"),
                    "产物数": len(item.get("artifacts") or []),
                }
                for item in execution_summary.get("operations") or []
            ]
            if operation_rows:
                st.dataframe(operation_rows, use_container_width=True, hide_index=True)
        else:
            comparison = execution_summary.get("comparison") or {}
            iteration_comparison = comparison.get("iteration") or {}
            result_columns = st.columns(4)
            result_columns[0].metric("第一轮 RMSE", f"{iteration_comparison.get('round_1_rmse', 0):.6f}")
            result_columns[1].metric("第二轮 RMSE", f"{iteration_comparison.get('round_2_rmse', 0):.6f}")
            result_columns[2].metric(
                "迭代改善", f"{iteration_comparison.get('relative_rmse_gain_percent', 0):.2f}%"
            )
            result_columns[3].metric("执行状态", status_label(execution_summary.get("status", "unknown")))
            st.caption(
                "第二轮计划由第一轮真实执行结果、质量反馈和计划调整生成；"
                "所有数值来自白名单确定性执行器。"
            )

    data_assets = [
        item for item in project.get("research_assets") or []
        if item.get("purpose") == "data" and item.get("parsing_status") == "parsed"
    ]
    if (
        data_assets
        and not project.get("planning_only", True)
        and project.get("research_mode") in {"data_analysis", "observational", "mixed_methods"}
        and phase in {"EXECUTION_WAITING", "DATA_ANALYSIS", "COMPLETED"}
    ):
        if st.button("使用项目内白名单工具复现数据分析", key=f"run_dataset_tools_{project.get('project_id')}"):
            try:
                _research_action(
                    backend_url,
                    f"/api/research/{project['project_id']}/run-dataset-tools",
                    {},
                )
            except BackendAPIError as exc:
                st.error(f"项目内分析失败：{exc.detail}")

    controlled_runs = project.get("controlled_python_runs") or []
    if controlled_runs:
        latest_python_run = controlled_runs[-1]
        st.subheader("受控 Python 分析记录")
        run_columns = st.columns(4)
        run_columns[0].metric("状态", status_label(latest_python_run.get("status")))
        run_columns[1].metric("耗时（毫秒）", latest_python_run.get("duration_ms", 0))
        run_columns[2].metric("峰值内存（MB）", latest_python_run.get("peak_memory_mb", 0))
        run_columns[3].metric("输出产物", len(latest_python_run.get("artifacts") or []))
        st.caption(f"代码 SHA-256：{latest_python_run.get('code_sha256') or '未记录'}")
        if latest_python_run.get("result") is not None:
            st.json(latest_python_run["result"])
        if latest_python_run.get("error"):
            st.warning(latest_python_run["error"])

    if (
        data_assets
        and not project.get("planning_only", True)
        and phase in {"EXECUTION_WAITING", "DATA_ANALYSIS", "CRITICAL_REVIEW", "SYNTHESIS", "COMPLETED"}
    ):
        try:
            sandbox_enabled = bool((get_json(backend_url, "/health") or {}).get(
                "controlled_python_sandbox_enabled", False
            ))
        except BackendAPIError:
            sandbox_enabled = False
        with st.expander("受控 Python 沙箱（实验性）", expanded=False):
            st.warning(
                "这是带 AST 审查、独立进程、禁导入/禁文件 API/禁网、超时和内存监控的受限分析器，"
                "但不等同于容器、虚拟机或独立系统账户。请勿粘贴密钥或私人信息。"
            )
            if not sandbox_enabled:
                st.info("后端默认关闭该实验功能。设置 AI_SCIENTIST_ENABLE_CONTROLLED_PYTHON=1 并重启后端后可使用。")
            asset_options = {f"{item.get('filename')} · {item.get('asset_id')}": item.get("asset_id") for item in data_assets}
            selected_asset_label = st.selectbox(
                "分析数据",
                list(asset_options),
                key=f"controlled_python_asset_{project.get('project_id')}",
            )
            example_code = (
                "overall = float(data['sepal_length_cm'].corr(data['petal_length_cm']))\n"
                "by_group = data.groupby('species').apply(\n"
                "    lambda frame: float(frame['sepal_length_cm'].corr(frame['petal_length_cm'])),\n"
                "    include_groups=False,\n"
                ").to_dict()\n"
                "result = {'overall_correlation': overall, 'by_species': by_group}"
            )
            sandbox_code = st.text_area(
                "分析代码（必须把最终值赋给 result）",
                value=example_code,
                height=190,
                key=f"controlled_python_code_{project.get('project_id')}",
            )
            limit_columns = st.columns(2)
            timeout_seconds = limit_columns[0].number_input(
                "超时（秒）", min_value=1, max_value=30, value=15,
                key=f"controlled_python_timeout_{project.get('project_id')}",
            )
            memory_limit_mb = limit_columns[1].number_input(
                "内存上限（MB）", min_value=256, max_value=1536, value=1024, step=128,
                key=f"controlled_python_memory_{project.get('project_id')}",
            )
            acknowledged = st.checkbox(
                "我确认代码不含密钥，并理解这不是容器级隔离。",
                key=f"controlled_python_ack_{project.get('project_id')}",
            )
            if st.button(
                "运行受控 Python 分析",
                disabled=not sandbox_enabled or not acknowledged or not sandbox_code.strip(),
                key=f"controlled_python_run_{project.get('project_id')}",
            ):
                try:
                    _research_action(
                        backend_url,
                        f"/api/research/{project['project_id']}/controlled-python",
                        {
                            "code": sandbox_code,
                            "asset_id": asset_options[selected_asset_label],
                            "timeout_seconds": int(timeout_seconds),
                            "memory_limit_mb": int(memory_limit_mb),
                        },
                    )
                except BackendAPIError as exc:
                    st.error(f"受控 Python 分析失败：{exc.detail}")

    if execution_summary and execution_summary.get("executor_binding") != "deterministic_data_analysis_v1":
        _render_execution_round("第一轮执行结果", execution_summary.get("round_1") or {})
        feedback_signals = execution_summary.get("feedback_signals") or []
        if feedback_signals:
            feedback = feedback_signal_view(feedback_signals[-1])
            st.markdown("### 第一轮反馈")
            st.markdown(f"**触发原因：** {feedback['trigger']}")
            st.markdown("**质量判断：** " + ("；".join(feedback["flags"]) or "未发现质量警告"))
            if feedback["evidence_refs"]:
                st.caption("证据引用：" + "、".join(feedback["evidence_refs"]))
        adjustments = execution_summary.get("plan_adjustments") or []
        if adjustments:
            st.markdown("### 第二轮计划调整")
            st.dataframe(plan_adjustment_rows(adjustments[-1]), use_container_width=True, hide_index=True)
        _render_execution_round("第二轮执行结果", execution_summary.get("round_2") or {})

    if execution_summary:
        with st.expander("查看原始结构化数据（开发者）", expanded=False):
            st.json(execution_summary)

    metrics = project.get("quality_metrics") or {}
    st.subheader("研究质量")
    quality_columns = st.columns(5)
    quality_columns[0].metric("证据覆盖率", metrics.get("evidence_coverage", 0))
    quality_columns[1].metric("假设完整度", metrics.get("hypothesis_completeness", 0))
    if metrics.get("total_conclusions", 0):
        quality_columns[2].metric("结论可追溯性", metrics.get("conclusion_traceability", 0))
    else:
        quality_columns[2].metric("结论可追溯性", "尚未评估")
    quality_columns[3].metric("审查最低评分", metrics.get("reviewer_min_score", 0))
    quality_columns[4].metric("不可验证来源", metrics.get("unverifiable_source_count", 0))
    st.caption(
        f"已验证的唯一来源：{metrics.get('verified_evidence_count', 0)}/"
        f"{metrics.get('unique_evidence_count', metrics.get('total_evidence_count', 0))}"
    )

    st.markdown(render_project_overview(project))
    st.subheader("研究进度")
    try:
        events = get_json(backend_url, f"/api/research/{project['project_id']}/events")
    except BackendAPIError:
        events = []
    visible_events = dedupe_user_events(events if isinstance(events, list) else [])
    if visible_events:
        for event in visible_events[-8:]:
            rendered = render_event_dict(event)
            if rendered:
                st.markdown(rendered)
    else:
        st.info("运行一个研究阶段后，这里将显示用户可见的进度。")
    active_job = _render_research_job_status(backend_url, project, show_debug)
    job_running = bool(active_job and active_job.get("status") in {"queued", "running"})

    _render_evidence_curation_gate(backend_url, project, job_running, show_debug)
    _render_revision_review_gate(backend_url, project, job_running, show_debug)

    action_columns = st.columns(3)
    human_gate = project.get("phase") in {
        "SEARCH_PLAN_REVIEW", "HUMAN_SOURCE_REVIEW", "HUMAN_REVISION_REVIEW", "HUMAN_APPROVAL"
    }
    if action_columns[0].button("运行下一阶段", type="primary", disabled=job_running or human_gate):
        _start_research_step_job(backend_url, project["project_id"])
    if project.get("phase") == "HUMAN_REVISION_REVIEW":
        st.info("当前需要您审查独立审查员提出的修订建议。请先在上方逐项决定如何处理。")
    if action_columns[1].button("刷新"):
        refresh_research_project(backend_url)
        st.rerun()
    if action_columns[2].button(
        "取消项目",
        disabled=job_running or project.get("phase") in {"COMPLETED", "FAILED", "CANCELLED"},
    ):
        _research_action(backend_url, f"/api/research/{project['project_id']}/cancel")

    if project.get("phase") == "HUMAN_APPROVAL":
        st.subheader("人工审查包")
        _render_research_asset_uploader(
            backend_url,
            project,
            upload_context="human_approval",
            key_prefix="approval_asset",
            title="为最终审核补充资料或数据",
        )
        try:
            review_package = get_json(
                backend_url, f"/api/research/{project['project_id']}/review-package"
            )
        except BackendAPIError as exc:
            review_package = None
            st.error("无法加载人工审查包，当前方案不能批准。")
            if show_debug:
                render_debug_object(exc.detail)
        if isinstance(review_package, dict):
            if review_package.get("ready_for_approval"):
                st.success("独立审查已通过且没有阻断问题，请核对下方各项研究产物后决定是否批准。")
            else:
                st.warning("当前审查包尚未满足批准条件，请先处理独立审查提出的问题。")
            versions = review_package.get("artifact_versions") or {}
            st.caption("本次审批将冻结：" + "；".join(f"{key} v{value}" for key, value in versions.items()))
            acknowledged = st.checkbox(
                "我已审阅研究问题、证据、主张映射、假设、方法、设计、分析、可复现性方案和独立审查结果。",
                key=f"approval_ack_{project['project_id']}_{review_package.get('package_id')}",
            )
            approval_columns = st.columns(2)
            if approval_columns[0].button(
                "批准当前版本",
                type="primary",
                disabled=job_running or not acknowledged or not review_package.get("ready_for_approval"),
                key=f"approve_package_{review_package.get('package_id')}",
            ):
                _research_action(
                    backend_url,
                    f"/api/research/{project['project_id']}/approve",
                    {"acknowledged": True, "expected_versions": versions},
                )
            if approval_columns[1].button(
                "暂不批准",
                disabled=job_running,
                key=f"defer_package_{review_package.get('package_id')}",
            ):
                _research_action(
                    backend_url,
                    f"/api/research/{project['project_id']}/defer-approval",
                    {"reason": "审查者选择稍后继续核对。"},
                )

    with st.expander("请求修订"):
        target = st.selectbox(
            "修订目标",
            ["question", "evidence", "hypothesis", "method", "design", "analysis", "reproducibility"],
            format_func=lambda value: {"question": "研究问题", "evidence": "证据", "hypothesis": "假设", "method": "研究方法", "design": "研究设计", "analysis": "分析方案", "reproducibility": "可复现性"}[value],
        )
        feedback = st.text_area("反馈意见", key="revision_feedback")
        if st.button("提交修订请求", disabled=job_running or project.get("phase") != "HUMAN_APPROVAL"):
            _research_action(
                backend_url,
                f"/api/research/{project['project_id']}/revise",
                {"target": target, "feedback": feedback},
            )

    if show_debug:
      with st.expander("结构化人工编辑（开发者）"):
        edit_target = st.selectbox(
            "编辑目标",
            ["question", "hypothesis", "study_design", "analysis_plan"],
            key="human_edit_target",
            format_func=lambda value: {
                "question": "研究问题",
                "hypothesis": "研究假设",
                "study_design": "研究设计",
                "analysis_plan": "分析计划",
            }[value],
        )
        hypothesis_options = [item.get("hypothesis_id", "") for item in project.get("hypotheses") or []]
        selected_hypothesis = ""
        if edit_target == "hypothesis":
            selected_hypothesis = st.selectbox("假设 ID", hypothesis_options, key="human_edit_hypothesis_id")
        edit_patch_text = st.text_area("补丁 JSON", value="{}", key="human_edit_patch")
        edit_reason = st.text_input("编辑原因", key="human_edit_reason")
        if st.button("保存人工编辑", disabled=job_running):
            try:
                patch = json.loads(edit_patch_text)
                if not isinstance(patch, dict):
                    raise ValueError("补丁 JSON 必须是一个对象。")
                endpoint = {
                    "question": f"/api/research/{project['project_id']}/question",
                    "study_design": f"/api/research/{project['project_id']}/study-design",
                    "analysis_plan": f"/api/research/{project['project_id']}/analysis-plan",
                }.get(edit_target)
                if edit_target == "hypothesis":
                    if not selected_hypothesis:
                        raise ValueError("请先选择一个假设。")
                    endpoint = f"/api/research/{project['project_id']}/hypotheses/{selected_hypothesis}"
                _research_patch(backend_url, endpoint or "", {"patch": patch, "reason": edit_reason})
            except BackendAPIError as exc:
                st.error(user_error_message(exc))

    with st.expander("提供数据"):
        paths = st.text_area("产物路径（每行一个）", key="data_paths")
        description = st.text_area("数据说明", key="data_description")
        data_type = st.text_input("数据类型", key="data_type")
        if st.button("登记数据", disabled=job_running):
            _research_action(
                backend_url,
                f"/api/research/{project['project_id']}/provide-data",
                {
                    "artifact_paths": [line.strip() for line in paths.splitlines() if line.strip()],
                    "description": description,
                    "data_type": data_type,
                },
            )

    capability_columns = st.columns(2)
    capability_columns[0].write("**当前可用能力**")
    capability_labels = {
        "web_search": "联网搜索",
        "web_extractor": "网页内容提取",
        "file_search": "上传资料检索",
        "dataset_inspector": "数据结构与质量检查",
        "statistical_analyzer": "白名单确定性统计分析",
        "categorical_analyzer": "分类频数、分组与列联分析",
        "time_series_analyzer": "时间序列趋势与滞后摘要",
        "text_analyzer": "文本语料词频与长度摘要",
        "data_visualizer": "确定性直方图与散点图",
        "deterministic_data_analysis_v1": "项目内确定性数据分析执行器",
        "artifact_store": "研究产物持久化",
        "python_executor": "受控 Python 分析沙箱（实验性）",
        "code_runner": "任意代码运行器",
        "citation_manager": "外部引用管理器",
    }
    available_labels = [capability_labels.get(name, name) for name in project.get("available_tools") or []]
    capability_columns[0].markdown("\n".join(f"- {item}" for item in available_labels) or "- 暂无")
    capability_columns[1].write("**尚未接入的能力**")
    available_capability_ids = set(project.get("available_tools") or [])
    missing_labels = [
        capability_labels.get(name, name)
        for name in project.get("missing_capabilities") or []
        if name not in available_capability_ids
    ]
    capability_columns[1].markdown("\n".join(f"- {item}" for item in missing_labels) or "- 暂无")
    if show_debug:
        st.write("**原始能力 ID**")
        render_debug_object({"available_tools": project.get("available_tools") or [], "missing_capabilities": project.get("missing_capabilities") or []})

    tabs = st.tabs([
        "研究问题", "证据与来源", "主张与证据", "假设与竞争性解释", "研究方法",
        "研究设计", "分析方案", "可复现性", "独立审查", "修订历史", "最终综合",
    ])
    with tabs[0]:
        question = project.get("question") or {}
        if question:
            st.markdown(f"**研究问题：** {question.get('normalized_question') or '尚未指定'}")
            st.markdown(f"**研究范围：** {question.get('scope') or '尚未指定'}")
            criteria = question.get("measurable_success_criteria") or []
            st.markdown("**成功标准：** " + ("；".join(criteria) if criteria else "尚未指定"))
        else:
            st.info("研究总监尚未完成研究问题形式化。")
    with tabs[1]:
        evidence = project.get("evidence") or []
        checkpoint = project.get("background_research_checkpoint") or {}
        candidates = checkpoint.get("candidates") or []
        snapshots = project.get("source_selection_snapshots") or []
        latest_selection = snapshots[-1] if snapshots else {}
        summary_columns = st.columns(3)
        summary_columns[0].metric("搜索候选资料", len(candidates))
        summary_columns[1].metric("用户保留资料", len(latest_selection.get("kept_candidate_ids") or []))
        summary_columns[2].metric("正式证据", len(evidence))
        st.markdown("### A. 搜索候选资料")
        st.caption("候选资料仍不是正式科研证据，必须经过人工选择、内容提取和来源验证。")
        for item in candidates:
            st.markdown(f"- {item.get('title') or item.get('url') or '未命名来源'}")
        st.markdown("### B. 用户保留资料")
        kept_ids = set(latest_selection.get("kept_candidate_ids") or [])
        kept_candidates = [item for item in candidates if item.get("candidate_id") in kept_ids]
        if kept_candidates:
            for item in kept_candidates:
                st.markdown(f"- {item.get('title') or item.get('url') or '未命名来源'}")
        else:
            st.caption("尚未提交人工来源选择。")
        st.markdown("### C. 正式证据")
        for item in evidence:
            title = item.get("title") or "未命名来源"
            url = item.get("source_url")
            with st.expander(f"{title} · 等级 {item.get('source_level', 'E')}"):
                st.markdown(item.get("summary") or "暂无摘要。")
                st.write(f"验证状态：{'已验证' if item.get('verified') else '待验证'}")
                if url:
                    st.markdown(f"[查看原始来源]({url})")
        if not evidence:
            st.caption("尚未形成经过验证的正式证据。")
    with tabs[2]:
        claims = project.get("claims") or []
        if not claims:
            st.info("尚未形成主张与证据映射。")
        for claim in claims:
            st.markdown(f"**{claim.get('statement', '未命名主张')}**")
            st.caption(f"状态：{status_label(claim.get('status', 'unknown'))}；支持证据：{', '.join(claim.get('supporting_evidence_ids') or []) or '无'}；反驳证据：{', '.join(claim.get('contradicting_evidence_ids') or []) or '无'}")
    with tabs[3]:
        hypotheses = project.get("hypotheses") or []
        if not hypotheses:
            st.info("尚未形成可检验假设。")
        for hypothesis in hypotheses:
            with st.expander(hypothesis.get("statement") or "未命名假设"):
                st.markdown(f"**机制：** {hypothesis.get('mechanism') or '尚未明确'}")
                _render_named_list("预测", hypothesis.get("predictions") or [])
                _render_named_list("可证伪条件", hypothesis.get("falsification_conditions") or [])
                _render_named_list("竞争性解释", hypothesis.get("alternative_explanations") or [])
    with tabs[4]:
        st.markdown(f"**研究模式：** {research_mode_label(project.get('research_mode'))}")
        st.markdown(f"**方法选择依据：** {project.get('method_rationale') or '尚未生成'}")
        _render_named_list("有效性威胁", project.get("validity_threats") or [])
        _render_named_list("必要控制", project.get("required_controls") or [])
    with tabs[5]:
        _render_mapping_sections(project.get("study_design") or {}, {
            "objective": "研究目标", "population_or_system": "研究对象或系统", "hypotheses_tested": "待检验假设",
            "variables": "变量", "controls": "控制条件", "comparison_groups": "比较组", "sampling_plan": "采样方案",
            "data_collection_plan": "数据收集", "measurement_plan": "测量方案", "quality_controls": "质量控制",
            "stopping_rules": "停止规则", "risks": "风险", "ethical_considerations": "伦理事项",
        })
    with tabs[6]:
        _render_mapping_sections(project.get("analysis_plan") or {}, {
            "objectives": "分析目标", "input_data": "输入数据", "preprocessing": "预处理", "metrics": "评价指标",
            "statistical_assumptions": "统计假设", "statistical_methods": "统计方法", "robustness_checks": "稳健性检查",
            "sensitivity_analysis": "敏感性分析", "uncertainty_quantification": "不确定性量化",
            "visualization_plan": "可视化方案", "success_criteria": "成功判据", "failure_criteria": "失败判据",
        })
    with tabs[7]:
        _render_mapping_sections(project.get("reproducibility_plan") or {}, {})
    with tabs[8]:
        reviews = project.get("reviews") or []
        if reviews:
            review = reviews[-1]
            if review.get("decision") == "approve":
                st.success("独立审查已批准当前方案。")
            elif review.get("decision") == "reject":
                st.error("独立审查已否决当前方案。")
            else:
                st.warning("独立审查要求进行定向修订。")
            issues = review.get("blocking_issues") or []
            _render_named_list("阻断问题", issues)
            _render_named_list("非阻断问题", review.get("non_blocking_issues") or [])
            _render_named_list("改进建议", review.get("recommendations") or [])
            score_columns = st.columns(3)
            score_columns[0].metric("证据质量", review.get("evidence_quality_score", 0))
            score_columns[1].metric("方法有效性", review.get("methodological_validity_score", 0))
            score_columns[2].metric("可复现性", review.get("reproducibility_score", 0))
        else:
            st.info("尚未运行独立审查。")
    with tabs[9]:
        plans = project.get("approved_revision_plans") or []
        verifications = {item.get("verification_id"): item for item in project.get("revision_verifications") or []}
        if not plans:
            st.info("尚未开始人工批准的修订轮次。")
            legacy_versions: dict[str, list[int]] = {}
            for artifact in project.get("artifacts") or []:
                version = artifact.get("version")
                if isinstance(version, int) and version > 1:
                    legacy_versions.setdefault(str(artifact.get("artifact_type")), []).append(version)
            if legacy_versions:
                st.markdown("### 旧版自动修订产物（未经过 completion criteria 验证）")
                for artifact_type, versions in sorted(legacy_versions.items()):
                    all_versions = [1, *sorted(set(versions))]
                    st.markdown(
                        f"- {_revision_target_label(artifact_type)}："
                        + " → ".join(f"v{version}" for version in all_versions)
                    )
        for plan in plans:
            st.markdown(f"### 独立审查 v{plan.get('review_version')} → 修订轮次 {plan.get('revision_cycle')}")
            summary = st.columns(4)
            summary[0].metric("人工接受", len(plan.get("approved_issues") or []))
            summary[1].metric("延期执行", len(plan.get("deferred_issues") or []))
            summary[2].metric("接受为局限", len(plan.get("accepted_as_limitation") or []))
            summary[3].metric("不同意", len(plan.get("rejected_issues") or []))
            for batch in plan.get("target_batches") or []:
                verification = verifications.get(batch.get("verification_id")) or {}
                criteria = verification.get("criteria_results") or []
                passed = len([item for item in criteria if item.get("passed")])
                version = batch.get("new_artifact_version")
                st.markdown(
                    f"- {_revision_target_label(batch.get('target'))}"
                    f"{f' v{version}' if version else ''}：{_revision_batch_status(batch.get('status'))}；"
                    f"验证 {passed}/{len(criteria)}"
                )
                if criteria:
                    with st.expander(f"查看{_revision_target_label(batch.get('target'))}验证详情"):
                        for criterion in criteria:
                            marker = "通过" if criterion.get("passed") else "未通过"
                            st.markdown(f"**{marker}：** {criterion.get('criterion')}")
                            if criterion.get("evidence"):
                                st.caption(f"依据：{criterion.get('evidence')}")
    with tabs[10]:
        conclusion = project.get("conclusion")
        if conclusion:
            _render_final_synthesis(conclusion)
        else:
            st.info("最终研究计划尚未生成。")
        render_research_downloads(backend_url, project)

    with st.expander("研究事件时间线"):
        events = get_json(backend_url, f"/api/research/{project['project_id']}/events")
        visible_events = dedupe_user_events(events if isinstance(events, list) else [])
        for event in visible_events:
            rendered = render_event_dict(event)
            if rendered:
                st.markdown(rendered)
        if show_debug:
            debug_rows = [
                {
                    "agent": event.get("agent_name"),
                    "status": event.get("status"),
                    "requested_model": event.get("requested_model"),
                    "actual_model": event.get("actual_model"),
                    "attempted": event.get("attempted_calls"),
                    "successful": event.get("successful_calls"),
                    "failed": event.get("failed_calls"),
                    "error_type": event.get("error_type"),
                    "local_time": format_local_datetime(event.get("started_at")),
                    "utc_time": format_utc_datetime(event.get("started_at")),
                }
                for event in events if isinstance(events, list)
            ]
            if debug_rows:
                st.dataframe(debug_rows, use_container_width=True, hide_index=True)
        artifacts = get_json(backend_url, f"/api/research/{project['project_id']}/artifacts")
        if show_debug:
            artifact_rows = [{"type": item.get("artifact_type"), "file": item.get("filename"), "version": item.get("version")} for item in normalize_records(artifacts)]
            if artifact_rows:
                st.dataframe(artifact_rows, use_container_width=True, hide_index=True)

    with st.expander("查看原始结构化数据（开发者）", expanded=False):
        raw_sections = {
            "项目概览": project,
            "证据与来源": {"evidence": project.get("evidence"), "source_selection_snapshots": project.get("source_selection_snapshots")},
            "独立审查": project.get("reviews") or [],
            "修订历史": {"revision_issues": project.get("revision_issues"), "approved_revision_plans": project.get("approved_revision_plans")},
            "最终综合": project.get("conclusion") or {},
        }
        raw_section = st.selectbox("数据范围", list(raw_sections), key=f"raw_section_{project.get('project_id')}")
        st.json(raw_sections[raw_section])


def _render_revision_review_gate(
    backend_url: str, project: dict, job_running: bool, show_debug: bool
) -> None:
    if project.get("phase") != "HUMAN_REVISION_REVIEW":
        return
    project_id = project.get("project_id")
    st.subheader("独立审查要求修订")
    st.info("独立审查没有否定这项研究，而是提出了需要您决定如何处理的问题。系统不会在您确认前自动重写研究产物。")
    _render_research_asset_uploader(
        backend_url,
        project,
        upload_context="revision_review",
        key_prefix="revision_asset",
        title="为本轮修订补充资料或数据",
    )
    for message in project.get("revision_recovery_messages") or []:
        st.warning(message)
    review = (project.get("reviews") or [{}])[-1]
    scores = st.columns(3)
    scores[0].metric("方法有效性", review.get("methodological_validity_score", 0))
    scores[1].metric("可复现性", review.get("reproducibility_score", 0))
    scores[2].metric("证据质量", review.get("evidence_quality_score", 0))
    _render_named_list("非阻断问题", review.get("non_blocking_issues") or [])
    _render_named_list("总体建议", review.get("recommendations") or [])

    active_plan_id = project.get("active_revision_plan_id")
    active_plan = next(
        (
            item
            for item in project.get("approved_revision_plans") or []
            if item.get("revision_plan_id") == active_plan_id
        ),
        None,
    )
    evidence_recovery_available = bool(
        active_plan
        and any(
            batch.get("target") == "evidence" and batch.get("status") == "needs_attention"
            for batch in active_plan.get("target_batches") or []
        )
    )
    if evidence_recovery_available:
        st.warning(
            "上一轮证据修订无法由自动修订器安全完成。请返回证据检索；"
            "您已上传的资料会保留并进入新的检索上下文。"
        )
        if st.button(
            "返回证据检索并保留已上传资料",
            type="primary",
            disabled=job_running,
            key=f"resume_evidence_research_{project_id}",
        ):
            _research_action(
                backend_url,
                f"/api/research/{project_id}/revision-review/resume-evidence-research",
                {},
            )
        return

    verifications = project.get("revision_verifications") or []
    latest_verification = verifications[-1] if verifications else None
    if latest_verification and not latest_verification.get("overall_passed"):
        failed_criteria = [
            item for item in latest_verification.get("criteria_results") or [] if not item.get("passed")
        ]
        st.warning(
            f"最近一次自动修订有 {len(failed_criteria)} 项验收标准未满足。"
            "重新确认后，系统会根据这些逐条反馈进行有限次数的定向补修。"
        )
        with st.expander("查看未通过的验收标准", expanded=True):
            for criterion in failed_criteria:
                st.markdown(f"**未通过：** {criterion.get('criterion') or '未命名标准'}")
                note = str(criterion.get("note") or "").split("Independent:", 1)[-1].strip()
                if note:
                    st.caption(note)

    st.markdown("### 建议修订计划")
    issues = project.get("revision_issues") or []
    if not issues:
        st.error("当前没有可提交的结构化修订问题，请刷新项目或查看后端迁移日志。")
        return
    decisions: list[dict] = []
    options = [
        "accept_ai",
        "accept_modified",
        "provide_content",
        "accept_limitation",
        "defer_execution",
        "reject",
    ]
    for index, issue in enumerate(issues):
        issue_id = issue.get("issue_id")
        classification = issue.get("classification")
        with st.container(border=True):
            st.markdown(f"**问题：** {issue.get('problem') or '未说明的问题'}")
            st.markdown(f"**严重性：** {_revision_classification_label(classification)}")
            st.markdown(f"**影响：** {issue.get('impact') or '尚未说明'}")
            _render_named_list("审查员建议", issue.get("reviewer_recommendations") or [])
            _render_named_list("验收标准", issue.get("completion_criteria") or [])
            st.markdown(f"**目标：** {_revision_target_label(issue.get('target'))}")
            default = "defer_execution" if classification == "execution_prerequisite" else (
                "accept_limitation" if classification in {"non_blocking", "optional"} else "accept_ai"
            )
            disposition = st.selectbox(
                "如何处理",
                options,
                index=options.index(default),
                format_func=lambda value: {
                    "accept_ai": "接受 AI 建议并自动修订",
                    "accept_modified": "接受，但修改修订要求",
                    "provide_content": "我自己提供补充信息",
                    "accept_limitation": "接受为研究局限性",
                    "defer_execution": "延期到执行阶段",
                    "reject": "不同意审查员此项建议",
                }[value],
                key=f"revision_disposition_{project_id}_{issue_id}",
            )
            instruction = ""
            reason = ""
            if disposition in {"accept_modified", "provide_content"}:
                instruction = st.text_area(
                    "修订指令或补充内容",
                    key=f"revision_instruction_{project_id}_{issue_id}",
                    placeholder="写明希望采用的具体规则、参数、检索式或方法。",
                )
            elif disposition in {"accept_limitation", "defer_execution", "reject"}:
                reason = st.text_area(
                    "说明",
                    key=f"revision_reason_{project_id}_{issue_id}",
                    placeholder="说明接受为局限、延期或不同意的理由。",
                )
            if show_debug:
                st.caption(
                    f"issue_id={issue_id} · source_actions={', '.join(issue.get('source_action_ids') or [])}"
                )
            decisions.append(
                {
                    "issue_id": issue_id,
                    "disposition": disposition,
                    "instruction": instruction,
                    "reason": reason,
                }
            )

    columns = st.columns(3)
    if columns[0].button(
        "确认修订计划并开始修订",
        type="primary",
        disabled=job_running,
        key=f"submit_revision_review_{project_id}",
    ):
        try:
            response = post_json(
                backend_url,
                f"/api/research/{project_id}/revision-review/submit",
                {"decisions": decisions},
            )
            if response.get("phase") == "REVISION":
                _start_research_step_job(backend_url, project_id)
            else:
                refresh_research_project(backend_url)
                st.rerun()
        except BackendAPIError as exc:
            detail = exc.detail
            message = detail.get("error_message") if isinstance(detail, dict) else str(detail)
            st.error(f"修订提交失败：{message or detail}")
            if show_debug:
                render_debug_object(
                    {"http_status": exc.status_code, "error_detail": detail}
                )
    if columns[1].button("稍后处理", disabled=job_running, key=f"defer_revision_review_{project_id}"):
        try:
            _research_action(
                backend_url,
                f"/api/research/{project_id}/revision-review/defer",
                {"reason": "用户选择稍后处理独立审查建议。"},
            )
        except BackendAPIError as exc:
            st.error(f"暂缓失败：{exc.detail}")
    if columns[2].button("取消项目", disabled=job_running, key=f"cancel_revision_review_{project_id}"):
        _research_action(backend_url, f"/api/research/{project_id}/revision-review/cancel", {})


def _revision_classification_label(value: str | None) -> str:
    return {
        "plan_blocking": "需要在方案定稿前处理",
        "execution_prerequisite": "未来执行阶段的前置要求",
        "non_blocking": "非阻断改进",
        "optional": "可选优化",
    }.get(value or "", value or "未分类")


def _revision_target_label(value: str | None) -> str:
    return {
        "question": "研究问题",
        "evidence": "证据",
        "hypothesis": "研究假设",
        "methodology": "方法学方案",
        "study_design": "研究设计",
        "analysis_plan": "分析方案",
        "reproducibility_plan": "可复现性方案",
        "execution_requirements": "执行阶段要求",
    }.get(value or "", value or "未指定")


def _revision_batch_status(value: str | None) -> str:
    return {
        "pending": "等待执行",
        "in_progress": "正在执行",
        "completed": "已验证完成",
        "needs_attention": "需要人工处理",
    }.get(value or "", value or "未知")


def _render_evidence_curation_gate(
    backend_url: str, project: dict, job_running: bool, show_debug: bool
) -> None:
    phase = project.get("phase")
    project_id = project.get("project_id")
    if phase == "SEARCH_PLAN_REVIEW":
        checkpoint = project.get("background_research_checkpoint") or {}
        plan = checkpoint.get("search_plan") or {}
        st.subheader("检索方案审查")
        _render_research_asset_uploader(
            backend_url,
            project,
            upload_context="search_plan_review",
            key_prefix="search_plan_asset",
            title="为检索审核补充资料或数据",
        )
        st.markdown(f"**当前研究问题：** {(project.get('question') or {}).get('normalized_question') or project.get('objective')}")
        status = plan.get("relevance_status")
        if status == "irrelevant":
            st.error("系统检测到检索方案可能偏离研究问题。请修改检索式或重新生成。")
        elif status == "partially_relevant":
            st.warning("检索方案只覆盖了部分核心概念，请重点核对。")
        else:
            st.info("AI 建议使用以下检索方案。联网搜索尚未开始。")
        _render_named_list("目标来源类型", plan.get("target_source_types") or [])
        _render_named_list("目标数据库", plan.get("preferred_databases") or [])
        _render_named_list("时间范围", plan.get("date_constraints") or [])
        st.markdown(f"**设计理由：** {plan.get('rationale') or '尚未说明'}")
        queries_text = st.text_area(
            "检索式（每行一条，可直接修改）",
            value="\n".join(plan.get("queries") or []),
            key=f"search_plan_queries_{project_id}_{plan.get('search_plan_id')}",
            height=160,
        )
        auto_future = st.checkbox(
            "以后本项目自动批准相关性检查通过的检索方案",
            value=bool(project.get("auto_approve_search_plan")),
            key=f"search_plan_auto_{project_id}",
        )
        columns = st.columns(2)
        if columns[0].button(
            "批准并开始检索",
            type="primary",
            disabled=job_running,
            key=f"approve_search_plan_{project_id}",
        ):
            _research_action(
                backend_url,
                f"/api/research/{project_id}/search-plan/approve",
                {
                    "queries": [line.strip() for line in queries_text.splitlines() if line.strip()],
                    "auto_approve_future": auto_future,
                },
            )
        if columns[1].button(
            "重新生成检索计划", disabled=job_running, key=f"regenerate_search_plan_{project_id}"
        ):
            _research_action(backend_url, f"/api/research/{project_id}/search-plan/regenerate", {})
        if show_debug:
            with st.expander("检索方案绑定信息（开发者）"):
                render_debug_object(
                    {
                        "project_id": plan.get("project_id"),
                        "question_hash": plan.get("question_hash"),
                        "version": plan.get("version"),
                        "planner_model": plan.get("planner_model"),
                        "relevance_note": plan.get("relevance_note"),
                    }
                )

    if phase == "HUMAN_SOURCE_REVIEW":
        checkpoint = project.get("background_research_checkpoint") or {}
        candidates = checkpoint.get("candidates") or []
        recommended = [item for item in candidates if item.get("ai_recommendation") == "keep"]
        excluded = [item for item in candidates if item.get("ai_recommendation") == "reject"]
        st.subheader("候选资料审查")
        summary = st.columns(3)
        summary[0].metric("候选来源", len(candidates))
        summary[1].metric("AI 初步推荐", len(recommended))
        summary[2].metric("正式证据", len(project.get("evidence") or []))
        st.info("AI 负责发现和说明资料，最终是否采用由你决定。只有保留的来源才会进入正式提取和验证。")
        if not candidates:
            records = checkpoint.get("query_records") or []
            completed_without_sources = len(
                [item for item in records if item.get("status") == "completed"]
            )
            timed_out = len([item for item in records if item.get("status") == "timeout"])
            failed = len([item for item in records if item.get("status") == "failed"])
            planned = len((checkpoint.get("search_plan") or {}).get("queries") or [])
            not_attempted = max(0, planned - len(records))
            st.warning(
                "本轮没有取得带 URL、DOI 或其他可核验标识的候选来源，系统不会把无来源的模型文本当作证据。"
                "你可以重新生成检索方案，或在下方登记 DOI、URL、论文标题并继续。"
            )
            st.caption(
                f"诊断：{completed_without_sources} 条查询完成但无来源元数据；"
                f"{timed_out} 条超时；{failed} 条失败；{not_attempted} 条未执行。"
            )
        for item in [*recommended, *[x for x in candidates if x not in recommended and x not in excluded]]:
            _render_source_candidate_card(project_id, item, initially_expanded=item in recommended)
        if excluded:
            with st.container(border=True):
                st.markdown(f"**AI 建议排除（{len(excluded)}）**")
                for item in excluded:
                    _render_source_candidate_card(project_id, item, initially_expanded=False)

        with st.expander("添加我自己的资料"):
            source_entries = st.text_area(
                "每行输入一个 DOI、PMID、arXiv ID、URL、论文标题或正式引用",
                key=f"human_sources_{project_id}",
            )
            if st.button("登记人工来源", key=f"add_human_sources_{project_id}"):
                _research_action(
                    backend_url,
                    f"/api/research/{project_id}/human-sources",
                    {"entries": [line.strip() for line in source_entries.splitlines() if line.strip()]},
                )
            _render_research_asset_uploader(
                backend_url,
                project,
                upload_context="source_review",
                key_prefix="source_review_asset",
                title="上传本地资料或数据文件",
                embedded=True,
            )

        note = st.text_area("本次资料筛选说明（可选）", key=f"source_selection_note_{project_id}")
        action_columns = st.columns(3)
        if action_columns[0].button(
            "保留选中资料并继续",
            type="primary",
            disabled=job_running or not candidates,
            key=f"submit_source_selection_{project_id}",
        ):
            decisions = []
            for item in candidates:
                candidate_id = item.get("candidate_id")
                decision = st.session_state.get(
                    f"source_decision_{project_id}_{candidate_id}",
                    _source_default_decision(item),
                )
                decisions.append(
                    {
                        "candidate_id": candidate_id,
                        "decision": decision,
                        "note": st.session_state.get(f"source_note_{project_id}_{candidate_id}", ""),
                        "rejection_reason": st.session_state.get(
                            f"source_rejection_{project_id}_{candidate_id}", ""
                        ) if decision == "reject" else "",
                    }
                )
            _research_action(
                backend_url,
                f"/api/research/{project_id}/source-selection",
                {"decisions": decisions, "selection_note": note},
            )
        if action_columns[1].button(
            "重新生成检索方案", disabled=job_running, key=f"research_sources_again_{project_id}"
        ):
            _research_action(backend_url, f"/api/research/{project_id}/search-plan/regenerate", {})
        if action_columns[2].button(
            "暂时停止研究", disabled=job_running, key=f"pause_source_review_{project_id}"
        ):
            st.info("项目保持在候选资料审查阶段，不会自动继续。")
        if show_debug:
            with st.expander("候选来源内部结构（开发者）"):
                render_debug_object(candidates)


def _render_source_candidate_card(project_id: str, item: dict, initially_expanded: bool) -> None:
    candidate_id = item.get("candidate_id")
    title = item.get("title") or item.get("url") or "未命名来源"
    with st.expander(title, expanded=initially_expanded):
        metadata = [
            item.get("journal_or_publisher") or item.get("source_domain") or "来源待核对",
            item.get("publication_year") or "年份未知",
            item.get("source_type") or "类型未知",
        ]
        st.caption(" · ".join(metadata))
        identifiers = []
        if item.get("doi"):
            identifiers.append(f"DOI: {item['doi']}")
        if item.get("pmid"):
            identifiers.append(f"PMID: {item['pmid']}")
        if item.get("arxiv_id"):
            identifiers.append(f"arXiv: {item['arxiv_id']}")
        if identifiers:
            st.write("；".join(identifiers))
        st.markdown(f"**AI 摘要：** {item.get('ai_summary') or item.get('snippet') or '暂无摘要'}")
        recommendation_label = {"keep": "建议保留", "reject": "建议排除", "uncertain": "需要人工判断"}.get(
            item.get("ai_recommendation"), "需要人工判断"
        )
        st.markdown(f"**AI 判断：** {recommendation_label}")
        st.markdown(f"**为什么：** {item.get('recommendation_reason') or '尚未说明'}")
        st.caption(
            f"相关性：{_relevance_label(item.get('relevance_score', 0))}；"
            f"验证状态：{_verification_label(item.get('verification_status'))}"
        )
        if item.get("url"):
            st.markdown(f"[打开来源]({item['url']})")
        decision = st.radio(
            "处理决定",
            ["keep", "reject", "defer"],
            index=["keep", "reject", "defer"].index(_source_default_decision(item)),
            format_func=lambda value: {"keep": "保留", "reject": "不保留", "defer": "暂缓决定"}[value],
            horizontal=True,
            key=f"source_decision_{project_id}_{candidate_id}",
        )
        if decision == "reject":
            st.selectbox(
                "排除原因",
                ["主题无关", "来源不可信", "不是一级研究", "重复来源", "不符合PICO", "内容不可访问", "用户认为质量不足", "其他"],
                key=f"source_rejection_{project_id}_{candidate_id}",
            )
        st.text_input("备注（可选）", key=f"source_note_{project_id}_{candidate_id}")


def _source_default_decision(item: dict) -> str:
    return {"keep": "keep", "reject": "reject", "uncertain": "defer"}.get(
        item.get("ai_recommendation"), "defer"
    )


def _relevance_label(value: float) -> str:
    return "高" if value >= 0.66 else "中" if value >= 0.33 else "低"


def _verification_label(value: str | None) -> str:
    return {"verified": "已验证", "partially_verified": "部分验证", "unverified": "待验证"}.get(
        value or "", "待核对"
    )


def _render_named_list(title: str, items: list) -> None:
    st.markdown(f"**{title}**")
    if items:
        st.markdown("\n".join(f"- {item}" for item in items))
    else:
        st.caption("尚未明确。")


def _render_mapping_sections(data: dict, labels: dict[str, str]) -> None:
    if not data:
        st.info("该研究产物尚未生成。")
        return
    default_labels = {
        "environment_specification": "运行环境",
        "software_and_versions": "软件与版本",
        "randomness_controls": "随机性控制",
        "data_provenance": "数据来源与谱系",
        "artifact_retention": "产物留存",
        "reproduction_steps": "复现步骤",
        "validation_checks": "验证检查",
        "reproducibility_plan": "复现计划",
        "missing_reproducibility_information": "尚缺信息",
        "assumptions": "假设条件",
        "limitations": "局限",
    }
    for key, value in data.items():
        if key in {"research_mode", "feasibility"}:
            st.markdown(f"**{labels.get(key, key.replace('_', ' '))}：** {value}")
            continue
        title = labels.get(key, default_labels.get(key, "其他说明"))
        values = value if isinstance(value, list) else [value]
        _render_named_list(title, [item for item in values if item not in (None, "", [], {})])


def render_research_downloads(backend_url: str, project: dict) -> None:
    """Render the project's one canonical Markdown download control."""

    try:
        report_md = get_text(backend_url, f"/api/research/{project['project_id']}/report.md")
        st.download_button(
            "下载研究计划",
            data=report_md,
            file_name="research_plan.md",
            mime="text/markdown",
            key=f"research_plan_download_{project['project_id']}",
        )
    except BackendAPIError:
        st.info("研究计划尚未生成。")


def _render_research_job_status(backend_url: str, project: dict, show_debug: bool) -> dict | None:
    job_id = st.session_state.get("research_job_id")
    if not job_id:
        return None
    try:
        job = get_json(backend_url, f"/api/research/jobs/{job_id}")
    except BackendAPIError as exc:
        st.session_state.research_job_id = None
        st.warning("无法加载研究阶段状态，请刷新项目后重试。")
        if show_debug:
            render_debug_object(exc.detail)
        return None
    if not isinstance(job, dict):
        return None

    status = job.get("status")
    phase = job.get("phase") or project.get("phase")
    if status == "queued":
        st.info(f"研究阶段已排队：{PHASE_LABELS.get(str(phase), phase)}")
        time.sleep(2)
        st.rerun()
    if status == "running":
        st.info(f"正在运行{PHASE_LABELS.get(str(phase), phase)}。包含联网搜索的背景研究可能需要几分钟。")
        time.sleep(3)
        st.rerun()
    if status == "completed":
        st.session_state.research_job_id = None
        refresh_research_project(backend_url)
        result = job.get("result") or {}
        if result.get("max_revision_exhausted"):
            st.error("项目已达到最大修订次数，但仍未通过独立审查。")
        elif result.get("revision_required"):
            st.warning(_revision_message(result))
        else:
            st.success("当前研究阶段已完成。")
        st.rerun()
    if status == "failed":
        st.session_state.research_job_id = None
        error = job.get("error") or {}
        message = str(error.get("error_message") or "")
        stage_substep = str(error.get("stage_substep") or "")
        if "timeout" in message.lower():
            st.error("Qwen 模型调用超时。已批准的修订计划会被保留，刷新后可直接重试，不消耗新的修订轮次。")
        elif stage_substep:
            st.error(_research_failure_message(stage_substep))
        else:
            st.error("研究阶段执行失败，项目保留在上一个已完成阶段。")
        if show_debug:
            render_debug_object(job)
    elif show_debug:
        render_debug_object(job)
    return job


def _research_failure_message(stage_substep: str) -> str:
    messages = {
        "model_output_parse": "主张映射输出未通过格式检查，已有证据已保留。",
        "schema_validation": "主张映射输出未通过结构验证，已有证据已保留。",
        "evidence_reference_validation": "部分主张引用了缺失或过期的证据，因此未写入不一致结果。",
        "claim_graph_build": "无法构建主张—证据图，因此没有写入不完整的图数据。",
        "artifact_save": "主张—证据映射已完成，但研究产物保存失败。",
        "project_state_update": "主张—证据映射已完成，但项目状态更新失败，可以安全重试。",
        "phase_transition": "研究内容已生成，但工作流状态未推进，可以安全重试。",
    }
    return messages.get(stage_substep, "研究阶段执行失败，项目保留在上一个已完成阶段。")


def _revision_message(result: dict) -> str:
    target = result.get("current_phase")
    if target == "HUMAN_REVISION_REVIEW":
        if result.get("stage_status") == "revision_needs_attention":
            return "AI 未能完整满足当前修订批次的验证标准，项目已返回人工修订审查。"
        return "独立审查提出了需要处理的问题。系统已暂停自动修订，等待您逐项审核修订建议。"
    target_text = {
        "BACKGROUND_RESEARCH": "背景证据研究阶段",
        "HYPOTHESIS_GENERATION": "假设生成阶段",
        "METHOD_SELECTION": "方法选择阶段",
        "STUDY_DESIGN": "研究设计阶段",
        "QUESTION_FORMULATION": "研究问题形式化阶段",
        "ANALYSIS_PLANNING": "分析规划阶段",
    }.get(str(target), "相应修订阶段")
    issues = result.get("blocking_issues") or []
    if issues:
        bullets = "\n".join(f"- {item}" for item in issues[:4])
        return f"独立审查要求修订，项目已返回{target_text}。\n\n主要阻断问题：\n{bullets}"
    return f"独立审查要求修订，项目已返回{target_text}。"

def _start_research_step_job(backend_url: str, project_id: str) -> None:
    try:
        created = post_json(backend_url, f"/api/research/{project_id}/step_async", timeout=30)
        st.session_state.research_job_id = created["job_id"]
        st.rerun()
    except BackendAPIError as exc:
        detail = exc.detail
        if isinstance(detail, dict) and detail.get("error") == "project_step_already_running":
            st.session_state.research_job_id = detail.get("job_id")
            st.warning("该项目已有一个研究阶段正在运行。")
            st.rerun()
        else:
            st.error("无法启动研究阶段。")
            if isinstance(detail, dict):
                render_debug_object(detail)
            else:
                st.caption(user_error_message(detail))


def _research_action(backend_url: str, path: str, payload: dict | None = None) -> None:
    try:
        with st.spinner("正在处理研究操作……"):
            timeout = RESEARCH_STEP_TIMEOUT if path.endswith("/step") else 120
            post_json(backend_url, path, payload, timeout=timeout)
        refresh_research_project(backend_url)
        st.rerun()
    except BackendAPIError as exc:
        st.error(user_error_message(exc))


def _research_patch(backend_url: str, path: str, payload: dict) -> None:
    try:
        url = f"{backend_url.rstrip('/')}{path}"
        response = requests.patch(url, json=payload, timeout=120)
        if not response.ok:
            raise BackendAPIError(response.status_code, _extract_error_detail(response))
        st.session_state.research_project = response.json()
        st.rerun()
    except BackendAPIError:
        raise


st.set_page_config(page_title="AI Scientist", layout="wide")

STATE_DEFAULTS = {
    "research_project_id": None,
    "research_project": None,
    "research_job_id": None,
    "research_objective": "",
    "research_domain_hint": "",
    "research_constraints_text": "",
    "research_seed_mode": "Auto",
    "research_custom_seed": 20260831,
    "research_planning_only": False,
    "research_example_case": "",
    "research_example_notice": "",
}
for key, value in STATE_DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value

st.sidebar.header("AI Scientist")
backend_url = st.sidebar.text_input("后端地址", value=DEFAULT_BACKEND_URL)
show_debug = st.sidebar.checkbox("开发者调试", value=False)
render_research_workspace(backend_url, show_debug)
