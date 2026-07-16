"""Streamlit frontend for three isolated Qwen application modes."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv

from src.ai_scientist.presentation import PHASE_LABELS, render_event_dict, render_project_overview
from src.model_utils import normalize_model_name


ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

DEFAULT_BACKEND_URL = "http://localhost:8000"
APP_MODES = ["Pure Qwen", "Qwen Search", "AI Scientist"]
RESEARCH_STEP_TIMEOUT = int(os.getenv("AI_SCIENTIST_FRONTEND_STEP_TIMEOUT", "600"))

SCIENTIST_MODEL_KEYS = {
    "research_director": ("scientist_director_model", "研究总监模型"),
    "evidence_researcher": ("scientist_research_model", "证据研究员模型"),
    "methodologist": ("scientist_methodologist_model", "方法学专家模型"),
    "hypothesis_scientist": ("scientist_hypothesis_model", "假设科学家模型"),
    "study_designer": ("scientist_designer_model", "研究设计师模型"),
    "analyst": ("scientist_analyst_model", "分析师模型"),
    "reproducibility_engineer": ("scientist_reproducibility_model", "可复现性工程师模型"),
    "skeptical_reviewer": ("scientist_reviewer_model", "独立审查员模型"),
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
                    },
                )
                st.session_state.research_project_id = created["project_id"]
                refresh_research_project(backend_url)
                st.rerun()
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
    columns[3].metric("修订轮次", project.get("iteration", 0))
    budget = project.get("budget") or {}
    columns[4].metric("模型调用", f"{budget.get('used_model_calls', 0)}/{budget.get('max_model_calls', 0)}")
    st.caption(f"项目 ID：{project.get('project_id')}")
    if project.get("model_overrides"):
        st.caption("该项目使用创建时保存的模型覆盖配置。")
        if show_debug:
            render_debug_object(project.get("model_overrides"))

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
    visible_events = [
        event
        for event in events
        if isinstance(events, list) and (show_debug or event.get("visibility") == "user")
    ]
    if visible_events:
        for event in visible_events[-8:]:
            st.markdown(render_event_dict(event))
    else:
        st.info("运行一个研究阶段后，这里将显示用户可见的进度。")
    active_job = _render_research_job_status(backend_url, project, show_debug)
    job_running = bool(active_job and active_job.get("status") in {"queued", "running"})

    action_columns = st.columns(5)
    if action_columns[0].button("运行下一阶段", type="primary", disabled=job_running):
        _start_research_step_job(backend_url, project["project_id"])
    if action_columns[1].button("批准", disabled=job_running or project.get("phase") != "HUMAN_APPROVAL"):
        _research_action(backend_url, f"/api/research/{project['project_id']}/approve")
    if action_columns[2].button("刷新"):
        refresh_research_project(backend_url)
        st.rerun()
    if action_columns[3].button(
        "取消项目",
        disabled=job_running or project.get("phase") in {"COMPLETED", "FAILED", "CANCELLED"},
    ):
        _research_action(backend_url, f"/api/research/{project['project_id']}/cancel")
    try:
        report_md = get_text(backend_url, f"/api/research/{project['project_id']}/report.md")
        action_columns[4].download_button(
            "下载研究计划",
            data=report_md,
            file_name="research_plan.md",
            mime="text/markdown",
        )
    except BackendAPIError:
        action_columns[4].button("下载研究计划", disabled=True)

    with st.expander("请求修订"):
        target = st.selectbox(
            "修订目标",
            ["question", "evidence", "hypothesis", "method", "design"],
            format_func=lambda value: {"question": "研究问题", "evidence": "证据", "hypothesis": "假设", "method": "研究方法", "design": "研究设计"}[value],
        )
        feedback = st.text_area("反馈意见", key="revision_feedback")
        if st.button("提交修订请求", disabled=job_running or project.get("phase") != "HUMAN_APPROVAL"):
            _research_action(
                backend_url,
                f"/api/research/{project['project_id']}/revise",
                {"target": target, "feedback": feedback},
            )

    with st.expander("结构化人工编辑"):
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

    tabs = st.tabs(["研究问题", "证据与假设", "规划与审查", "研究计划", "事件日志"])
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
        claims = project.get("claims") or []
        hypotheses = project.get("hypotheses") or []
        st.markdown(f"已保留证据：**{len(evidence)}** 条；已映射关键主张：**{len(claims)}** 条。")
        st.markdown(f"候选假设：**{len(hypotheses)}** 个。")
        source_levels = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
        for item in evidence:
            level = item.get("source_level", "E")
            source_levels[level] = source_levels.get(level, 0) + 1
        st.markdown("来源等级分布：" + "；".join(f"{k}:{v}" for k, v in source_levels.items()))
        verified_count = len([item for item in evidence if item.get("verification_status") == "verified" and not item.get("duplicate_of")])
        unique_count = len([item for item in evidence if not item.get("duplicate_of")])
        st.markdown(f"已验证的唯一来源：**{verified_count}/{unique_count}**")
        if show_debug:
            rows = [{"title": item.get("title", ""), "level": item.get("source_level", "E"), "verification_status": item.get("verification_status", "unverified"), "verification_method": item.get("verification_method", "none"), "doi": item.get("doi"), "pmid": item.get("pmid"), "url": item.get("source_url")} for item in evidence]
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True)
    with tabs[2]:
        st.markdown(f"**研究模式：** {project.get('research_mode') or '尚未选择'}")
        st.markdown(f"**方法选择依据：** {project.get('method_rationale') or '尚未生成'}")
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
            st.markdown("阻断问题：" + ("；".join(issues) if issues else "无"))
        else:
            st.info("尚未运行独立审查。")
    with tabs[3]:
        conclusion = project.get("conclusion")
        if conclusion:
            st.markdown("科学综合报告已生成，可下载 Markdown 报告查看完整内容。")
        else:
            st.info("最终研究计划尚未生成。")
    with tabs[4]:
        events = get_json(backend_url, f"/api/research/{project['project_id']}/events")
        visible_events = [
            event
            for event in events if isinstance(events, list)
            if show_debug or event.get("visibility") == "user"
        ]
        for event in visible_events:
            st.markdown(render_event_dict(event))
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
        try:
            report_md = get_text(backend_url, f"/api/research/{project['project_id']}/report.md")
            st.download_button(
                "下载研究计划",
                data=report_md,
                file_name="research_plan.md",
                mime="text/markdown",
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
        if stage_substep:
            st.error(_research_failure_message(stage_substep))
        elif "timeout" in message.lower():
            st.error("研究阶段运行超时，项目保留在上一个已完成阶段。")
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
