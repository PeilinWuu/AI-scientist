"""Chat-first Streamlit UI for FlowScientist."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

from src.agents.dialogue_orchestrator import DialogueOrchestrator
from src.config import settings
from src.llm import get_llm_status
from src.state.conversation_state import ConversationState


def new_run_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"


def create_state() -> ConversationState:
    llm_status = get_llm_status()
    backend = {
        "llm_provider": llm_status["llm_provider"],
        "llm_transport": llm_status["llm_transport"],
        "llm_model": llm_status["llm_model"],
        "llm_base_url": llm_status["llm_base_url"],
        "is_mock": llm_status["mock_mode"],
    }
    run_id = new_run_id()
    run_dir = settings.runs_dir / run_id
    return ConversationState.create(run_id, run_dir, backend)


def sync_web_search_mode() -> None:
    """Copy the Streamlit widget value into the business search state."""

    st.session_state.web_search_mode = st.session_state.web_search_mode_control


def real_qwen_run(state: ConversationState) -> bool:
    return (
        state.llm_backend.get("llm_provider") == "qwen"
        and state.llm_backend.get("llm_transport") == "curl"
        and state.llm_backend.get("is_mock") is False
        and state.total_llm_calls > 0
        and state.total_tool_calls > 0
    )


def action_to_message(action: str) -> str:
    """Translate a suggested action button into an explicit user request."""

    mapping = {
        "generate_plan": "请基于上面的研究任务生成实验任务规划。",
        "design_adapter": "请设计 CFD/FreeFlow adapter 的数据接口。",
        "confirm_run_soft_swimmer": "我确认运行当前简化 soft-swimmer 示例工具。",
        "generate_report": "请生成一份阶段报告。",
        "plot_efficiency": "把已有结果画成效率柱状图。",
        "plot_speed_energy": "把刚才结果画成速度-能耗散点图。",
        "continue_next_round": "请根据刚才结果继续下一轮实验。",
    }
    return mapping.get(action, action)


def render_message_payload(message: dict, show_developer_debug: bool, message_index: int) -> None:
    """Render only user-facing response-composer fields in the main chat."""

    st.write(message.get("content", ""))

    for section in message.get("sections", []) or []:
        title = section.get("title") or "说明"
        content = section.get("content") or ""
        if content:
            st.markdown(f"**{title}**")
            st.write(content)

    for table in message.get("tables", []) or []:
        rows = table.get("rows") or []
        if rows:
            if table.get("title"):
                st.markdown(f"**{table['title']}**")
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

    figure_path = message.get("figure_path")
    if figure_path and Path(figure_path).exists():
        st.image(figure_path)
    for figure in message.get("figures", []) or []:
        path = figure.get("figure_path")
        if path and Path(path).exists():
            st.image(path, caption=figure.get("caption") or None)

    actions = message.get("suggested_actions") or []
    if actions:
        cols = st.columns(min(len(actions), 4))
        for index, action in enumerate(actions):
            label = action.get("label") or action.get("action") or "执行"
            action_id = action.get("action") or label
            if cols[index % len(cols)].button(label, key=f"action_{message_index}_{index}_{action_id}"):
                st.session_state.queued_user_message = action_to_message(action_id)
                st.rerun()

    if show_developer_debug and message.get("raw_debug"):
        with st.expander("Developer debug: raw decision / audit payload", expanded=False):
            st.json(message["raw_debug"])


st.set_page_config(page_title="FlowScientist", layout="wide")

if "conversation_state" not in st.session_state:
    st.session_state.conversation_state = create_state()
if "web_search_mode_control" not in st.session_state:
    st.session_state.web_search_mode_control = "off"
if "web_search_mode" not in st.session_state:
    st.session_state.web_search_mode = "off"
if "reset_web_search_mode_control" not in st.session_state:
    st.session_state.reset_web_search_mode_control = False
if st.session_state.reset_web_search_mode_control:
    st.session_state.web_search_mode_control = "off"
    st.session_state.web_search_mode = "off"
    st.session_state.reset_web_search_mode_control = False

state: ConversationState = st.session_state.conversation_state
llm_status = get_llm_status()
queued_user_message = st.session_state.pop("queued_user_message", None)
state.web_search_mode = st.session_state.get("web_search_mode", "off")

with st.sidebar:
    st.header("FlowScientist State")
    st.caption(f"Run ID: `{state.run_id}`")
    st.write("**Current research goal**")
    st.write(state.research_goal or "Not clarified yet")
    st.write("**Current constraints**")
    st.json(state.constraints or {})
    st.write(f"**Current intent:** {state.current_intent or 'unknown'}")
    st.write(f"**Current skill:** {state.current_skill or 'unknown'}")
    st.write(f"**Tool execution allowed:** {str(state.tool_execution_allowed).lower()}")
    permission = state.last_tool_permission or {}
    st.write(f"**Tool requires confirmation:** {str(permission.get('requires_confirmation', False)).lower()}")
    st.caption(f"Tool policy reason: {permission.get('reason', 'n/a')}")
    st.write(f"**Planning preference:** {state.planning_preference or 'unknown'}")
    st.write(f"**Target metric:** {state.target_metric or 'unknown'}")
    st.metric("Tool calls", state.total_tool_calls)
    st.metric("LLM calls", state.total_llm_calls)
    search_mode = st.radio(
        "联网搜索",
        options=["off", "this_turn", "always_on"],
        format_func=lambda value: {
            "off": "不联网",
            "this_turn": "仅本轮联网",
            "always_on": "始终联网，直到关闭",
        }[value],
        horizontal=True,
        key="web_search_mode_control",
        on_change=sync_web_search_mode,
    )
    business_search_mode = st.session_state.get("web_search_mode", "off")
    if business_search_mode != state.web_search_mode:
        state.web_search_mode = business_search_mode
        state.last_user_search_choice = business_search_mode
        state.save()
    search_capability = "enabled" if llm_status.get("qwen_enable_search", False) else "disabled"
    st.write(f"**Qwen Web Search Capability:** {search_capability}")
    st.write(f"**User Search Mode:** {state.web_search_mode}")
    st.write(f"**Last Qwen Search Used:** {str(state.last_qwen_search_used).lower()}")
    st.write(f"**Last Search Trigger:** {state.last_search_trigger or 'user_search_off'}")
    st.write(f"**Search Strategy:** {llm_status.get('qwen_search_strategy') or 'none'}")
    show_developer_debug = st.checkbox("Show developer debug panels", value=False)
    st.divider()
    st.write(f"**LLM Provider:** {llm_status['llm_provider']}")
    st.write(f"**Model:** {llm_status['llm_model']}")
    st.write(f"**Transport:** {llm_status['llm_transport']}")
    st.write(f"**Mock mode:** {str(llm_status['mock_mode']).lower()}")
    if llm_status.get("status_error"):
        st.error(llm_status["status_error"])
    if llm_status["mock_mode"]:
        st.error(
            "Mock mode is for development only and cannot be used for competition submission."
        )
    if st.button("Start new conversation"):
        st.session_state.conversation_state = create_state()
        st.session_state.web_search_mode = "off"
        st.session_state.reset_web_search_mode_control = True
        st.rerun()
    if show_developer_debug and state.last_decision_trace:
        with st.expander("Decision trace", expanded=False):
            st.json(state.last_decision_trace)

st.title("FlowScientist")
st.caption(
    "Qwen-powered conversational AI Scientist for general fluid simulation and "
    "flow-field optimization. The soft-swimmer simulator is one demo tool."
)

st.subheader("Audit Evidence")
audit_cols = st.columns(4)
audit_cols[0].metric("Total LLM calls", state.total_llm_calls)
audit_cols[1].metric("Total tool calls", state.total_tool_calls)
audit_cols[2].metric("Mock mode", str(state.llm_backend.get("is_mock", True)).lower())
audit_cols[3].metric("REAL_QWEN_RUN", str(real_qwen_run(state)).lower())
st.write(f"LLM call logs: `{Path(state.run_dir) / 'llm_calls'}`")
st.write(f"Tool call logs: `{Path(state.run_dir) / 'tool_calls'}`")
if show_developer_debug:
    st.write(f"Decision trace: `{Path(state.run_dir) / 'decision_trace.jsonl'}`")
if state.last_qwen_response_excerpt:
    st.caption(f"Last Qwen response excerpt: {state.last_qwen_response_excerpt}")
if show_developer_debug and state.last_tool_result:
    with st.expander("Developer debug: last tool result", expanded=False):
        st.json(state.last_tool_result)
if state.llm_backend.get("is_mock", True):
    st.error("This run is not valid for competition submission because it is in mock mode.")
elif state.total_llm_calls == 0:
    st.info("No Qwen call has been recorded in this conversation yet.")
elif state.total_tool_calls == 0:
    st.info("This conversation has real Qwen evidence but no tool call yet. That is expected for capability or conceptual questions.")

st.divider()

for message_index, message in enumerate(state.messages):
    role = message["role"]
    chat_role = "assistant" if role == "tool" else role
    with st.chat_message(chat_role):
        if role == "tool":
            st.markdown(f"**Tool called:** `{message.get('tool_name', 'unknown')}`")
        if message.get("search_used"):
            st.info("已启用 Qwen 联网搜索")
        render_message_payload(message, show_developer_debug, message_index)

user_message = st.chat_input(
    "Ask about fluid simulation, plan an experiment, request a tool run, or visualize results..."
)
effective_user_message = queued_user_message or user_message
if effective_user_message:
    orchestrator = DialogueOrchestrator(state)
    web_search_mode = st.session_state.get("web_search_mode", "off")
    with st.spinner("FlowScientist is reasoning with Qwen and tools..."):
        try:
            state = orchestrator.handle_user_message(
                effective_user_message,
                web_search_mode=web_search_mode,
            )
            st.session_state.conversation_state = state
            if web_search_mode == "this_turn":
                st.session_state.web_search_mode = "off"
                st.session_state.reset_web_search_mode_control = True
            st.rerun()
        except Exception as exc:  # noqa: BLE001 - Streamlit should show actionable failure.
            st.error("前端运行出错，请查看调试信息。")
            if show_developer_debug:
                st.exception(exc)

st.divider()
st.subheader("Quick Workflow")
quick_goal = st.text_input(
    "One-click 3-round goal",
    value="Optimize soft swimmer efficiency while keeping energy cost and unstable motion constrained.",
)
if st.button("Run 3 Qwen-guided rounds"):
    orchestrator = DialogueOrchestrator(state)
    web_search_mode = st.session_state.get("web_search_mode", "off")
    state.web_search_mode = web_search_mode
    with st.spinner("Running three Qwen-guided dialogue/tool rounds..."):
        try:
            state = orchestrator.run_three_rounds(quick_goal)
            st.session_state.conversation_state = state
            if web_search_mode == "this_turn":
                st.session_state.web_search_mode = "off"
                st.session_state.reset_web_search_mode_control = True
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error("前端运行出错，请查看调试信息。")
            if show_developer_debug:
                st.exception(exc)
