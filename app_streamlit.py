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


ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

DEFAULT_BACKEND_URL = "http://localhost:8000"
PURE_MODEL_OPTIONS = ["qwen-turbo", "qwen-plus", "qwen-plus-latest"]
SEARCH_MODEL_OPTIONS = ["qwen3.7-plus", "qwen3.7-max", "qwen3.6-plus", "qwen3.5-plus"]
APP_MODES = ["Pure Qwen", "Qwen Search", "AI Scientist"]
RESEARCH_STEP_TIMEOUT = int(os.getenv("AI_SCIENTIST_FRONTEND_STEP_TIMEOUT", "600"))


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
            st.info("No debug details.")
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
            st.info("当前接口未返回可验证搜索来源。")
        for source in sources:
            st.markdown(f"**[{source.get('index', '')}] {source.get('title') or '(untitled)'}**")
            st.write(source.get("site_name") or "(unknown site)")
            if source.get("url"):
                st.write(source["url"])
            if source.get("snippet"):
                st.caption(source["snippet"])
    with st.expander("Response metadata", expanded=False):
        render_debug_object(metadata)


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
        constraints_text = st.text_area(
            "补充要求与约束",
            value="",
            key="research_constraints_text",
            placeholder="例如：研究对象为软件工程师，周期最多 2 周，可获得项目管理系统、代码仓库和问卷数据，需要区分相关性和因果关系。",
        )
        max_iterations = st.number_input("Max iterations", min_value=0, max_value=10, value=2)
        planning_only = st.checkbox("Planning only", value=True)
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

    metrics = project.get("quality_metrics") or {}
    st.subheader("Research Quality")
    quality_columns = st.columns(5)
    quality_columns[0].metric("Evidence coverage", metrics.get("evidence_coverage", 0))
    quality_columns[1].metric("Hypothesis completeness", metrics.get("hypothesis_completeness", 0))
    quality_columns[2].metric("Conclusion traceability", metrics.get("conclusion_traceability", 0))
    quality_columns[3].metric("Reviewer min score", metrics.get("reviewer_min_score", 0))
    quality_columns[4].metric("Unverifiable sources", metrics.get("unverifiable_source_count", 0))

    st.markdown(render_project_overview(project))
    st.subheader("研究日志")
    messages = project.get("stage_messages") or []
    if messages:
        for message in messages[-8:]:
            st.markdown(message)
    else:
        st.info("运行阶段后，这里会显示各个科研角色的自然语言反馈。")

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
            "下载研究方案",
            data=report_md,
            file_name="research_plan.md",
            mime="text/markdown",
        )
    except BackendAPIError:
        action_columns[4].button("下载研究方案", disabled=True)

    with st.expander("要求修改"):
        target = st.selectbox("Revision target", ["question", "evidence", "hypothesis", "method", "design"])
        feedback = st.text_area("Feedback", key="revision_feedback")
        if st.button("提交修改请求", disabled=job_running or project.get("phase") != "HUMAN_APPROVAL"):
            _research_action(
                backend_url,
                f"/api/research/{project['project_id']}/revise",
                {"target": target, "feedback": feedback},
            )

    with st.expander("结构化人工编辑"):
        edit_target = st.selectbox(
            "Edit target",
            ["question", "hypothesis", "study_design", "analysis_plan"],
            key="human_edit_target",
        )
        hypothesis_options = [item.get("hypothesis_id", "") for item in project.get("hypotheses") or []]
        selected_hypothesis = ""
        if edit_target == "hypothesis":
            selected_hypothesis = st.selectbox("Hypothesis ID", hypothesis_options, key="human_edit_hypothesis_id")
        edit_patch_text = st.text_area("Patch JSON", value="{}", key="human_edit_patch")
        edit_reason = st.text_input("Reason", key="human_edit_reason")
        if st.button("保存人工编辑", disabled=job_running):
            try:
                patch = json.loads(edit_patch_text)
                if not isinstance(patch, dict):
                    raise ValueError("Patch JSON must be an object.")
                endpoint = {
                    "question": f"/api/research/{project['project_id']}/question",
                    "study_design": f"/api/research/{project['project_id']}/study-design",
                    "analysis_plan": f"/api/research/{project['project_id']}/analysis-plan",
                }.get(edit_target)
                if edit_target == "hypothesis":
                    if not selected_hypothesis:
                        raise ValueError("Select a hypothesis first.")
                    endpoint = f"/api/research/{project['project_id']}/hypotheses/{selected_hypothesis}"
                _research_patch(backend_url, endpoint or "", {"patch": patch, "reason": edit_reason})
            except BackendAPIError as exc:
                st.error(exc.detail if isinstance(exc, BackendAPIError) else str(exc))

    with st.expander("提供数据"):
        paths = st.text_area("Artifact paths（每行一个）", key="data_paths")
        description = st.text_area("Description", key="data_description")
        data_type = st.text_input("Data type", key="data_type")
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
    capability_columns[0].write("**当前可用**")
    capability_columns[0].write(["联网检索", "网页读取", "产物保存"])
    capability_columns[1].write("**尚未接入**")
    capability_columns[1].write(["文件分析", "Python 执行", "统计分析", "代码运行"])
    if show_debug:
        st.write("**Raw capability IDs**")
        render_debug_object({"available_tools": project.get("available_tools") or [], "missing_capabilities": project.get("missing_capabilities") or []})

    tabs = st.tabs(["研究问题", "证据与假设", "方案与审查", "研究方案", "事件记录"])
    with tabs[0]:
        question = project.get("question") or {}
        if question:
            st.markdown(f"**研究问题**：{question.get('normalized_question') or '尚未明确'}")
            st.markdown(f"**研究范围**：{question.get('scope') or '仍需细化'}")
            criteria = question.get("measurable_success_criteria") or []
            st.markdown("**成功标准**：" + ("；".join(criteria) if criteria else "尚未明确。"))
        else:
            st.info("研究总监尚未完成问题整理。")
    with tabs[1]:
        evidence = project.get("evidence") or []
        claims = project.get("claims") or []
        hypotheses = project.get("hypotheses") or []
        st.markdown(f"证据研究员已保留 **{len(evidence)}** 条证据，整理出 **{len(claims)}** 条关键主张。")
        st.markdown(f"假设科学家已形成 **{len(hypotheses)}** 个候选假设。")
        source_levels = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
        for item in evidence:
            level = item.get("source_level", "E")
            source_levels[level] = source_levels.get(level, 0) + 1
        st.markdown("来源等级分布：" + "、".join(f"{k}:{v}" for k, v in source_levels.items()))
        if show_debug:
            rows = [{"title": item.get("title", ""), "level": item.get("source_level", "E"), "verified": item.get("verified", False)} for item in evidence]
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True)
    with tabs[2]:
        st.markdown(f"**主要研究模式**：{project.get('research_mode') or '尚未选择'}")
        st.markdown(f"**方法学判断**：{project.get('method_rationale') or '尚未形成。'}")
        reviews = project.get("reviews") or []
        if reviews:
            review = reviews[-1]
            st.markdown(f"独立审查决定：**{review.get('decision')}**。")
            issues = review.get("blocking_issues") or []
            st.markdown("阻断问题：" + ("；".join(issues) if issues else "暂无。"))
        else:
            st.info("独立审查尚未完成。")
    with tabs[3]:
        conclusion = project.get("conclusion")
        if conclusion:
            st.markdown("科学综合已生成。请下载 Markdown 研究方案查看完整内容。")
        else:
            st.info("最终研究方案尚未生成。")
    with tabs[4]:
        events = get_json(backend_url, f"/api/research/{project['project_id']}/events")
        for event in events if isinstance(events, list) else []:
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
                "下载研究方案",
                data=report_md,
                file_name="research_plan.md",
                mime="text/markdown",
            )
        except BackendAPIError:
            st.info("研究方案尚未生成。")


def _render_research_job_status(backend_url: str, project: dict, show_debug: bool) -> dict | None:
    job_id = st.session_state.get("research_job_id")
    if not job_id:
        return None
    try:
        job = get_json(backend_url, f"/api/research/jobs/{job_id}")
    except BackendAPIError as exc:
        st.session_state.research_job_id = None
        st.warning("Research stage status could not be loaded. Refresh the project before retrying.")
        if show_debug:
            render_debug_object(exc.detail)
        return None
    if not isinstance(job, dict):
        return None

    status = job.get("status")
    phase = job.get("phase") or project.get("phase")
    if status == "queued":
        st.info(f"Research stage queued: {phase}")
        time.sleep(2)
        st.rerun()
    if status == "running":
        st.info(f"Running {phase}. Background research with web search may take several minutes.")
        time.sleep(3)
        st.rerun()
    if status == "completed":
        st.session_state.research_job_id = None
        refresh_research_project(backend_url)
        st.success("Research stage completed.")
        st.rerun()
    if status == "failed":
        st.session_state.research_job_id = None
        st.error("Research stage timed out or failed. The project remains at the last complete phase; refresh and retry.")
        if show_debug:
            render_debug_object(job)
    elif show_debug:
        render_debug_object(job)
    return job


def _start_research_step_job(backend_url: str, project_id: str) -> None:
    try:
        created = post_json(backend_url, f"/api/research/{project_id}/step_async", timeout=30)
        st.session_state.research_job_id = created["job_id"]
        st.rerun()
    except BackendAPIError as exc:
        detail = exc.detail
        if isinstance(detail, dict) and detail.get("error") == "project_step_already_running":
            st.session_state.research_job_id = detail.get("job_id")
            st.warning("A research stage is already running for this project.")
            st.rerun()
        else:
            st.error("Research stage could not be started.")
            if isinstance(detail, dict):
                render_debug_object(detail)
            else:
                st.write(detail)


def _research_action(backend_url: str, path: str, payload: dict | None = None) -> None:
    try:
        with st.spinner("正在处理当前研究阶段..."):
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
    "research_job_id": None,
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
