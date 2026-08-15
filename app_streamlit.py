"""Streamlit frontend for three isolated Qwen application modes."""

from __future__ import annotations

import json
import base64
import os
import time
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv

from src.ai_scientist.presentation import (
    PHASE_LABELS,
    dedupe_user_events,
    render_event_dict,
    render_project_overview,
)
from src.ui_time import format_local_datetime, format_utc_datetime
from src.model_utils import normalize_model_name


ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

DEFAULT_BACKEND_URL = "http://localhost:8000"
APP_MODES = ["Pure Qwen", "Qwen Search", "AI Scientist"]
RESEARCH_STEP_TIMEOUT = int(os.getenv("AI_SCIENTIST_FRONTEND_STEP_TIMEOUT", "600"))
RESEARCH_ASSET_EXTENSIONS = ["pdf", "md", "txt", "csv", "tsv", "json", "xml", "xlsx", "xls"]
RESEARCH_ASSET_MAX_BYTES = int(os.getenv("AI_SCIENTIST_MAX_ASSET_BYTES", str(25 * 1024 * 1024)))

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


def _save_research_assets(
    backend_url: str,
    project_id: str,
    uploads: list,
    purpose: str,
    description: str,
    upload_context: str,
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
                upload_context,
            )
            if saved:
                st.success(f"已保存 {len(saved)} 个文件。")
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
                if item.get("description"):
                    st.caption(str(item.get("description")))
                parsed = item.get("parsed_content") or {}
                if parsed:
                    st.caption(
                        f"解析器：{parsed.get('parser_name')} · {parsed.get('summary')}"
                    )
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
                            st.error(exc.detail)


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


def chat_history() -> list[dict[str, str]]:
    return [
        {"role": item["role"], "content": item["content"]}
        for item in st.session_state.messages
        if item.get("role") in {"user", "assistant"}
    ]


def render_search_debug(metadata: dict) -> None:
    with st.expander("搜索来源", expanded=bool(metadata.get("sources"))):
        sources = metadata.get("sources") or []
        if not sources:
            st.info("当前 API 响应未包含可验证的搜索来源。")
        for source in sources:
            st.markdown(f"**[{source.get('index', '')}] {source.get('title') or '（无标题）'}**")
            st.write(source.get("site_name") or "（未知网站）")
            if source.get("url"):
                st.write(source["url"])
            if source.get("snippet"):
                st.caption(source["snippet"])
    with st.expander("响应元数据", expanded=False):
        render_debug_object(metadata)


def render_chat_mode(backend_url: str, mode: str, show_debug: bool) -> None:
    search_enabled = mode == "Qwen Search"
    config = get_model_config(backend_url)
    if search_enabled:
        default_model = model_default(config, "qwen_search", os.getenv("LLM_SEARCH_MODEL", "qwen3.7-plus"))
        key = "qwen_search_model_input"
        if key not in st.session_state:
            st.session_state[key] = default_model
        if st.sidebar.button("重置搜索模型"):
            st.session_state[key] = default_model
            st.rerun()
        st.sidebar.text_input(
            "搜索模型名称",
            key=key,
            placeholder="例如：qwen3.7-plus",
            help="请输入 DashScope 模型 ID。搜索模型必须支持当前 Responses API 的联网搜索工具。",
        )
        model = effective_model_input(key, default_model)
        if not model:
            return
        if st.sidebar.button("测试搜索模型"):
            render_model_test_result(test_model(backend_url, model, "search"))
    else:
        default_model = model_default(config, "pure_qwen", os.getenv("LLM_MODEL", "qwen-turbo"))
        key = "pure_qwen_model_input"
        if key not in st.session_state:
            st.session_state[key] = default_model
        if st.sidebar.button("重置模型"):
            st.session_state[key] = default_model
            st.rerun()
        st.sidebar.text_input(
            "模型名称",
            key=key,
            placeholder="例如：qwen-plus",
            help="请输入 DashScope 模型 ID。留空时使用服务器默认模型。",
        )
        model = effective_model_input(key, default_model)
        if not model:
            return
        if st.sidebar.button("测试模型"):
            render_model_test_result(test_model(backend_url, model, "chat"))

    if not search_enabled:
        st.session_state.search_previous_response_id = None
        st.session_state.last_search_model = None
    elif st.session_state.last_search_model not in (None, model):
        st.session_state.search_previous_response_id = None
    if search_enabled:
        st.session_state.last_search_model = model

    if st.sidebar.button("清空对话"):
        st.session_state.messages = []
        st.session_state.search_previous_response_id = None
        st.session_state.last_search_model = None
        st.session_state.last_debug_payload = None
        st.session_state.last_endpoint = None
        st.session_state.last_chat_endpoint = None
        st.session_state.last_response_metadata = None
        st.rerun()

    if show_debug and st.sidebar.button("查看发送载荷"):
        if st.session_state.last_debug_payload is None:
            st.sidebar.info("暂无载荷，请先发送一条消息。")
        else:
            st.sidebar.write(f"**聊天接口：** `{st.session_state.last_chat_endpoint}`")
            st.sidebar.write(f"**调试接口：** `{st.session_state.last_endpoint}`")
            st.sidebar.json(st.session_state.last_debug_payload)
            if st.session_state.last_response_metadata:
                st.sidebar.write("**最近一次响应元数据：**")
                st.sidebar.json(st.session_state.last_response_metadata)

    st.title({"Pure Qwen": "纯 Qwen 对话", "Qwen Search": "Qwen 联网搜索"}.get(mode, mode))
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])

    user_input = st.chat_input("输入消息")
    if not user_input:
        return
    if search_enabled:
        payload = {
            "message": user_input,
            "model": model,
            "previous_response_id": st.session_state.search_previous_response_id,
        }
        chat_endpoint = "/api/chat_search"
        debug_endpoint = "/api/debug_search_payload"
    else:
        payload = {"message": user_input, "history": chat_history(), "model": model}
        chat_endpoint = "/api/chat"
        debug_endpoint = "/api/debug_payload"

    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.write(user_input)
    try:
        debug_payload = post_json(backend_url, debug_endpoint, payload) if show_debug else None
        st.session_state.last_debug_payload = debug_payload
        st.session_state.last_endpoint = debug_endpoint if show_debug else None
        st.session_state.last_chat_endpoint = chat_endpoint
        with st.spinner("Qwen 正在回复……"):
            response = post_json(backend_url, chat_endpoint, payload)
        metadata_keys = ["mode", "model", "response_id", "request_id", "search_used", "sources", "tool_usage"]
        st.session_state.last_response_metadata = {
            key: response.get(key) for key in metadata_keys if key in response
        }
        if search_enabled:
            st.session_state.search_previous_response_id = response.get("response_id")
        reply = response.get("reply", "")
        assistant_record = {"role": "assistant", "content": reply}
        st.session_state.messages.append(assistant_record)
        with st.chat_message("assistant"):
            st.write(reply)
        if show_debug:
            with st.expander("发送给 Qwen 的调试载荷", expanded=False):
                st.write(f"**聊天接口：** `{chat_endpoint}`")
                st.write(f"**调试接口：** `{debug_endpoint}`")
                render_debug_object(debug_payload)
            if search_enabled:
                render_search_debug(st.session_state.last_response_metadata or {})
    except BackendAPIError as exc:
        st.error("后端 Qwen 调用失败。")
        if isinstance(exc.detail, dict):
            render_debug_object(exc.detail)
        else:
            st.write(exc.detail)
    except Exception as exc:  # noqa: BLE001
        st.error("前端或后端调用失败。")
        if show_debug:
            st.exception(exc)


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


def render_research_workspace(backend_url: str, show_debug: bool) -> None:
    st.title("AI Scientist")
    model_defaults = render_scientist_model_config(backend_url)
    st.caption("多个 Qwen 角色通过状态机协作。当前模式只生成科研规划，不虚构实验或分析结果。")

    with st.expander("创建研究项目", expanded=not bool(st.session_state.research_project_id)):
        existing_project_id = st.text_input("加载已有项目 ID", key="existing_research_project_id")
        if st.button("加载项目"):
            try:
                st.session_state.research_project_id = existing_project_id.strip()
                refresh_research_project(backend_url)
                st.rerun()
            except BackendAPIError as exc:
                st.error(exc.detail)
        objective = st.text_area("研究目标", key="research_objective")
        domain_hint = st.text_input("领域提示（可选）", key="research_domain_hint")
        constraints_text = st.text_area(
            "补充要求与约束",
            value="",
            key="research_constraints_text",
            placeholder="例如：目标人群、可用数据、时间范围、约束条件，以及是否允许因果推断。",
        )
        max_iterations = st.number_input("最大修订次数", min_value=0, max_value=10, value=2)
        planning_only = st.checkbox("仅规划模式", value=True)
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
        if st.button("创建项目", type="primary"):
            try:
                created = post_json(
                    backend_url,
                    "/api/research/start",
                    {
                        "objective": objective,
                        "domain_hint": domain_hint or None,
                        "constraints_text": constraints_text,
                        "constraints": {},
                        "model_overrides": scientist_model_overrides(model_defaults),
                        "max_iterations": int(max_iterations),
                        "planning_only": planning_only,
                        "evidence_review_mode": evidence_review_mode,
                    },
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
                st.error(exc.detail if isinstance(exc, BackendAPIError) else str(exc))

    project = st.session_state.research_project
    if not project:
        st.info("请先创建项目，再逐阶段运行科研工作流。")
        return

    st.subheader("项目状态")
    columns = st.columns(5)
    columns[0].metric("当前阶段", PHASE_LABELS.get(project.get("phase", ""), project.get("phase", "")))
    columns[1].metric("研究模式", project.get("research_mode") or "待选择")
    columns[2].metric("研究领域", project.get("domain") or "通用")
    pending_cycle = 1 if project.get("phase") == "HUMAN_REVISION_REVIEW" and project.get("revision_issues") else 0
    columns[3].metric("修订轮次", project.get("iteration", 0) + pending_cycle)
    budget = project.get("budget") or {}
    columns[4].metric("模型调用", f"{budget.get('used_model_calls', 0)}/{budget.get('max_model_calls', 0)}")
    st.caption(f"项目 ID：{project.get('project_id')}")
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
    )

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
                st.error(exc.detail if isinstance(exc, BackendAPIError) else str(exc))

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
    capability_columns[0].write(["联网搜索", "网页内容提取", "研究产物持久化"])
    capability_columns[1].write("**尚未接入的能力**")
    capability_columns[1].write(["文件分析", "Python 执行", "统计分析", "代码运行器"])
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
            st.caption("尚未形成经过验证的正式 Evidence。")
    with tabs[2]:
        claims = project.get("claims") or []
        if not claims:
            st.info("尚未形成主张与证据映射。")
        for claim in claims:
            st.markdown(f"**{claim.get('statement', '未命名主张')}**")
            st.caption(f"状态：{claim.get('status', 'unknown')}；支持证据：{', '.join(claim.get('supporting_evidence_ids') or []) or '无'}；反驳证据：{', '.join(claim.get('contradicting_evidence_ids') or []) or '无'}")
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
        st.markdown(f"**研究模式：** {project.get('research_mode') or '尚未选择'}")
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
            st.markdown(f"### Independent Review v{plan.get('review_version')} → 修订轮次 {plan.get('revision_cycle')}")
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
            _render_mapping_sections(conclusion, {
                "supported_findings": "有证据支持的发现", "unsupported_or_inconclusive": "尚无定论",
                "uncertainties": "不确定性", "limitations": "局限", "next_questions": "后续问题",
            })
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
            _render_named_list("Reviewer 建议", issue.get("reviewer_recommendations") or [])
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
                    "reject": "不同意 Reviewer 此项建议",
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
        for item in [*recommended, *[x for x in candidates if x not in recommended and x not in excluded]]:
            _render_source_candidate_card(project_id, item, initially_expanded=item in recommended)
        if excluded:
            with st.expander(f"AI 建议排除（{len(excluded)}）"):
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
            "重新检索", disabled=job_running, key=f"research_sources_again_{project_id}"
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
                st.write(detail)


def _research_action(backend_url: str, path: str, payload: dict | None = None) -> None:
    try:
        with st.spinner("正在处理研究操作……"):
            timeout = RESEARCH_STEP_TIMEOUT if path.endswith("/step") else 120
            post_json(backend_url, path, payload, timeout=timeout)
        refresh_research_project(backend_url)
        st.rerun()
    except BackendAPIError as exc:
        st.error(exc.detail)


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


st.set_page_config(page_title="Qwen 科研工作台", layout="wide")

STATE_DEFAULTS = {
    "messages": [],
    "last_debug_payload": None,
    "last_endpoint": None,
    "last_chat_endpoint": None,
    "last_response_metadata": None,
    "search_previous_response_id": None,
    "last_search_model": None,
    "research_project_id": None,
    "research_project": None,
    "research_job_id": None,
}
for key, value in STATE_DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value

st.sidebar.header("Qwen 科研工作台")
backend_url = st.sidebar.text_input("后端地址", value=DEFAULT_BACKEND_URL)
mode = st.sidebar.radio(
    "应用模式",
    APP_MODES,
    horizontal=False,
    format_func=lambda value: {
        "Pure Qwen": "纯 Qwen 对话",
        "Qwen Search": "Qwen 联网搜索",
        "AI Scientist": "AI Scientist",
    }[value],
)
show_debug = st.sidebar.checkbox("开发者调试", value=False)

if mode == "AI Scientist":
    st.session_state.search_previous_response_id = None
    render_research_workspace(backend_url, show_debug)
else:
    render_chat_mode(backend_url, mode, show_debug)
