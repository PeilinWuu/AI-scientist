"""Streamlit frontend for three isolated Qwen application modes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

DEFAULT_BACKEND_URL = "http://localhost:8000"
PURE_MODEL_OPTIONS = ["qwen-turbo", "qwen-plus", "qwen-plus-latest"]
SEARCH_MODEL_OPTIONS = ["qwen3.7-plus", "qwen3.7-max", "qwen3.6-plus", "qwen3.5-plus"]
APP_MODES = ["Pure Qwen", "Qwen Search", "AI Scientist"]


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


def post_json(backend_url: str, path: str, payload: dict | None = None) -> dict:
    url = f"{backend_url.rstrip('/')}{path}"
    response = requests.post(url, json=payload, timeout=180)
    if not response.ok:
        raise BackendAPIError(response.status_code, _extract_error_detail(response))
    return response.json()


def get_json(backend_url: str, path: str) -> object:
    url = f"{backend_url.rstrip('/')}{path}"
    response = requests.get(url, timeout=60)
    if not response.ok:
        raise BackendAPIError(response.status_code, _extract_error_detail(response))
    return response.json()


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
            st.info("当前接口未返回可验证搜索来源。")
        for source in sources:
            st.markdown(f"**[{source.get('index', '')}] {source.get('title') or '(untitled)'}**")
            st.write(source.get("site_name") or "(unknown site)")
            if source.get("url"):
                st.write(source["url"])
            if source.get("snippet"):
                st.caption(source["snippet"])
    with st.expander("Response metadata", expanded=False):
        st.json(metadata)


def render_chat_mode(backend_url: str, mode: str, show_debug: bool) -> None:
    search_enabled = mode == "Qwen Search"
    if search_enabled:
        default_model = os.getenv("LLM_SEARCH_MODEL", "qwen3.7-plus")
        default_index = SEARCH_MODEL_OPTIONS.index(default_model) if default_model in SEARCH_MODEL_OPTIONS else 0
        model = st.sidebar.selectbox(
            "Model", SEARCH_MODEL_OPTIONS, index=default_index, key="search_model_control"
        )
    else:
        default_model = os.getenv("LLM_MODEL", "qwen-turbo")
        default_index = PURE_MODEL_OPTIONS.index(default_model) if default_model in PURE_MODEL_OPTIONS else 0
        model = st.sidebar.selectbox(
            "Model", PURE_MODEL_OPTIONS, index=default_index, key="pure_model_control"
        )

    if not search_enabled:
        st.session_state.search_previous_response_id = None
        st.session_state.last_search_model = None
    elif st.session_state.last_search_model not in (None, model):
        st.session_state.search_previous_response_id = None
    if search_enabled:
        st.session_state.last_search_model = model

    if st.sidebar.button("Clear conversation"):
        st.session_state.messages = []
        st.session_state.search_previous_response_id = None
        st.session_state.last_search_model = None
        st.session_state.last_debug_payload = None
        st.session_state.last_endpoint = None
        st.session_state.last_chat_endpoint = None
        st.session_state.last_response_metadata = None
        st.rerun()

    if show_debug and st.sidebar.button("查看发送 payload"):
        if st.session_state.last_debug_payload is None:
            st.sidebar.info("还没有可查看的 payload。请先发送一条消息。")
        else:
            st.sidebar.write(f"**Chat endpoint:** `{st.session_state.last_chat_endpoint}`")
            st.sidebar.write(f"**Debug endpoint:** `{st.session_state.last_endpoint}`")
            st.sidebar.json(st.session_state.last_debug_payload)
            if st.session_state.last_response_metadata:
                st.sidebar.write("**Last response metadata:**")
                st.sidebar.json(st.session_state.last_response_metadata)

    st.title(mode)
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])

    user_input = st.chat_input("请输入消息")
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
        with st.spinner("Qwen is replying..."):
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
            with st.expander("Debug payload sent to Qwen", expanded=False):
                st.write(f"**Chat endpoint:** `{chat_endpoint}`")
                st.write(f"**Debug endpoint:** `{debug_endpoint}`")
                st.json(debug_payload)
            if search_enabled:
                render_search_debug(st.session_state.last_response_metadata or {})
    except BackendAPIError as exc:
        st.error("后端 Qwen 调用失败。")
        st.json(exc.detail) if isinstance(exc.detail, dict) else st.write(exc.detail)
    except Exception as exc:  # noqa: BLE001
        st.error("前端或后端调用出错。")
        if show_debug:
            st.exception(exc)


def refresh_research_project(backend_url: str) -> dict | None:
    project_id = st.session_state.research_project_id
    if not project_id:
        return None
    project = get_json(backend_url, f"/api/research/{project_id}")
    st.session_state.research_project = project
    return project  # type: ignore[return-value]


def render_research_workspace(backend_url: str, show_debug: bool) -> None:
    st.title("AI Scientist")
    st.caption("多 Qwen 角色按状态机协作；当前版本执行研究规划，不伪造实验或分析结果。")

    with st.expander("创建研究项目", expanded=not bool(st.session_state.research_project_id)):
        existing_project_id = st.text_input("加载已有 Project ID", key="existing_research_project_id")
        if st.button("加载项目"):
            try:
                st.session_state.research_project_id = existing_project_id.strip()
                refresh_research_project(backend_url)
                st.rerun()
            except BackendAPIError as exc:
                st.error(exc.detail)
        objective = st.text_area("Research objective", key="research_objective")
        domain_hint = st.text_input("Domain hint（可选）", key="research_domain_hint")
        constraints_text = st.text_area("Constraints JSON", value="{}", key="research_constraints")
        max_iterations = st.number_input("Max iterations", min_value=0, max_value=10, value=2)
        planning_only = st.checkbox("Planning only", value=True)
        if st.button("创建项目", type="primary"):
            try:
                constraints = json.loads(constraints_text)
                if not isinstance(constraints, dict):
                    raise ValueError("Constraints JSON 必须是对象。")
                created = post_json(
                    backend_url,
                    "/api/research/start",
                    {
                        "objective": objective,
                        "domain_hint": domain_hint or None,
                        "constraints": constraints,
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
        st.info("创建项目后，可以逐阶段运行研究流程。")
        return

    st.subheader("项目状态")
    columns = st.columns(5)
    columns[0].metric("Phase", project.get("phase", ""))
    columns[1].metric("Mode", project.get("research_mode") or "pending")
    columns[2].metric("Domain", project.get("domain") or "general")
    columns[3].metric("Iteration", project.get("iteration", 0))
    budget = project.get("budget") or {}
    columns[4].metric("Model calls", f"{budget.get('used_model_calls', 0)}/{budget.get('max_model_calls', 0)}")
    st.caption(f"Project ID: {project.get('project_id')}")

    action_columns = st.columns(5)
    if action_columns[0].button("运行下一阶段", type="primary"):
        _research_action(backend_url, f"/api/research/{project['project_id']}/step")
    if action_columns[1].button("批准", disabled=project.get("phase") != "HUMAN_APPROVAL"):
        _research_action(backend_url, f"/api/research/{project['project_id']}/approve")
    if action_columns[2].button("刷新"):
        refresh_research_project(backend_url)
        st.rerun()
    if action_columns[3].button("取消项目", disabled=project.get("phase") in {"COMPLETED", "FAILED", "CANCELLED"}):
        _research_action(backend_url, f"/api/research/{project['project_id']}/cancel")
    action_columns[4].download_button(
        "导出研究方案",
        data=json.dumps(project, ensure_ascii=False, indent=2),
        file_name=f"{project['project_id']}_research_plan.json",
        mime="application/json",
    )

    with st.expander("要求修改"):
        target = st.selectbox("Revision target", ["question", "evidence", "hypothesis", "method", "design"])
        feedback = st.text_area("Feedback", key="revision_feedback")
        if st.button("提交修改请求", disabled=project.get("phase") != "HUMAN_APPROVAL"):
            _research_action(
                backend_url,
                f"/api/research/{project['project_id']}/revise",
                {"target": target, "feedback": feedback},
            )

    with st.expander("提供数据"):
        paths = st.text_area("Artifact paths（每行一个）", key="data_paths")
        description = st.text_area("Description", key="data_description")
        data_type = st.text_input("Data type", key="data_type")
        if st.button("登记数据"):
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
    capability_columns[0].write("**Available capabilities**")
    capability_columns[0].write(project.get("available_tools") or [])
    capability_columns[1].write("**Missing capabilities**")
    capability_columns[1].write(project.get("missing_capabilities") or [])

    tabs = st.tabs(["研究问题", "证据与主张", "假设", "方法与设计", "Reviewer", "科学综合", "事件与产物"])
    with tabs[0]:
        question = project.get("question")
        if question:
            st.write(f"**Normalized question:** {question.get('normalized_question', '')}")
            st.write(f"**Scope:** {question.get('scope', '')}")
            st.write("**Success criteria:**", question.get("measurable_success_criteria") or [])
            st.write("**Assumptions:**", question.get("assumptions") or [])
            st.write("**Unknowns:**", question.get("unknowns") or [])
        else:
            st.info("尚未完成问题形式化。")
    with tabs[1]:
        evidence = project.get("evidence") or []
        claims = project.get("claims") or []
        if evidence:
            st.dataframe(evidence, use_container_width=True)
        else:
            st.info("尚无证据记录。")
        st.write("**Claim-Evidence mapping**")
        st.dataframe(claims, use_container_width=True) if claims else st.info("尚无主张记录。")
        st.write("**Conflicting evidence:**", project.get("conflicting_evidence") or [])
        st.write("**Evidence gaps:**", project.get("evidence_gaps") or [])
    with tabs[2]:
        hypotheses = project.get("hypotheses") or []
        if not hypotheses:
            st.info("尚未生成假设。")
        for hypothesis in hypotheses:
            with st.expander(hypothesis.get("statement", "Hypothesis")):
                st.write("**Predictions:**", hypothesis.get("predictions") or [])
                st.write("**Falsification conditions:**", hypothesis.get("falsification_conditions") or [])
                st.write("**Alternative explanations:**", hypothesis.get("alternative_explanations") or [])
    with tabs[3]:
        st.write(f"**Research mode:** {project.get('research_mode') or 'pending'}")
        st.write(f"**Rationale:** {project.get('method_rationale') or ''}")
        st.write("**Validity threats:**", project.get("validity_threats") or [])
        st.write("**Required controls:**", project.get("required_controls") or [])
        st.write("**Study design**")
        st.json(project.get("study_design") or {})
        st.write("**Analysis plan**")
        st.json(project.get("analysis_plan") or {})
        st.write("**Reproducibility plan**")
        st.json(project.get("reproducibility_plan") or {})
    with tabs[4]:
        reviews = project.get("reviews") or []
        if reviews:
            st.json(reviews[-1])
        else:
            st.info("尚未进行独立审查。")
    with tabs[5]:
        conclusion = project.get("conclusion")
        st.json(conclusion) if conclusion else st.info("尚未生成科学综合。")
    with tabs[6]:
        events = get_json(backend_url, f"/api/research/{project['project_id']}/events")
        artifacts = get_json(backend_url, f"/api/research/{project['project_id']}/artifacts")
        st.write("**事件时间线**")
        for event in events if isinstance(events, list) else []:
            st.write(f"{event.get('started_at')} · {event.get('phase')} · {event.get('agent_name')} · {event.get('status')}")
            if show_debug:
                safe_keys = [
                    "event_id", "agent_name", "requested_model", "actual_model", "fallback_used",
                    "phase", "schema_valid", "tool_names", "token_usage",
                ]
                st.json({key: event.get(key) for key in safe_keys})
        st.write("**产物列表**")
        st.dataframe(artifacts, use_container_width=True) if artifacts else st.info("尚无产物。")


def _research_action(backend_url: str, path: str, payload: dict | None = None) -> None:
    try:
        with st.spinner("正在处理当前研究阶段..."):
            post_json(backend_url, path, payload)
        refresh_research_project(backend_url)
        st.rerun()
    except BackendAPIError as exc:
        st.error(exc.detail)


st.set_page_config(page_title="Qwen Research Shell", layout="wide")

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
}
for key, value in STATE_DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value

st.sidebar.header("Qwen Research Shell")
backend_url = st.sidebar.text_input("Backend URL", value=DEFAULT_BACKEND_URL)
mode = st.sidebar.radio("Mode", APP_MODES, horizontal=False)
show_debug = st.sidebar.checkbox("Developer debug", value=False)

if mode == "AI Scientist":
    st.session_state.search_previous_response_id = None
    render_research_workspace(backend_url, show_debug)
else:
    render_chat_mode(backend_url, mode, show_debug)
