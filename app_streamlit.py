"""Chat-first Streamlit UI for FlowScientist."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

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


def real_qwen_run(state: ConversationState) -> bool:
    return (
        state.llm_backend.get("llm_provider") == "qwen"
        and state.llm_backend.get("llm_transport") == "curl"
        and state.llm_backend.get("is_mock") is False
        and state.total_llm_calls > 0
        and state.total_tool_calls > 0
    )


st.set_page_config(page_title="FlowScientist", layout="wide")

if "conversation_state" not in st.session_state:
    st.session_state.conversation_state = create_state()

state: ConversationState = st.session_state.conversation_state
llm_status = get_llm_status()

with st.sidebar:
    st.header("FlowScientist State")
    st.caption(f"Run ID: `{state.run_id}`")
    st.write("**Current research goal**")
    st.write(state.research_goal or "Not clarified yet")
    st.write("**Current constraints**")
    st.json(state.constraints or {})
    st.write(f"**Planning preference:** {state.planning_preference or 'unknown'}")
    st.write(f"**Target metric:** {state.target_metric or 'unknown'}")
    st.metric("Tool calls", state.total_tool_calls)
    st.metric("LLM calls", state.total_llm_calls)
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
        st.rerun()

st.title("FlowScientist")
st.caption("Qwen-powered conversational experiment planner for soft-swimmer flow optimization")

st.subheader("Audit Evidence")
audit_cols = st.columns(4)
audit_cols[0].metric("Total LLM calls", state.total_llm_calls)
audit_cols[1].metric("Total tool calls", state.total_tool_calls)
audit_cols[2].metric("Mock mode", str(state.llm_backend.get("is_mock", True)).lower())
audit_cols[3].metric("REAL_QWEN_RUN", str(real_qwen_run(state)).lower())
st.write(f"LLM call logs: `{Path(state.run_dir) / 'llm_calls'}`")
st.write(f"Tool call logs: `{Path(state.run_dir) / 'tool_calls'}`")
if state.last_qwen_response_excerpt:
    st.caption(f"Last Qwen response excerpt: {state.last_qwen_response_excerpt}")
if state.last_tool_result:
    with st.expander("Last tool result", expanded=False):
        st.json(state.last_tool_result)
if state.total_llm_calls == 0 or state.llm_backend.get("is_mock", True):
    st.error("This run is not valid for competition submission.")

st.divider()

for message in state.messages:
    role = message["role"]
    chat_role = "assistant" if role == "tool" else role
    with st.chat_message(chat_role):
        if role == "tool":
            st.markdown("**Tool result**")
        st.write(message["content"])

user_message = st.chat_input(
    "Describe your soft-swimmer research goal, constraints, or feedback..."
)
if user_message:
    orchestrator = DialogueOrchestrator(state)
    with st.spinner("FlowScientist is reasoning with Qwen and tools..."):
        try:
            state = orchestrator.handle_user_message(user_message)
            st.session_state.conversation_state = state
            st.rerun()
        except Exception as exc:  # noqa: BLE001 - Streamlit should show actionable failure.
            st.error(str(exc))

st.divider()
st.subheader("Quick Workflow")
quick_goal = st.text_input(
    "One-click 3-round goal",
    value="Optimize soft swimmer efficiency while keeping energy cost and unstable motion constrained.",
)
if st.button("Run 3 Qwen-guided rounds"):
    orchestrator = DialogueOrchestrator(state)
    with st.spinner("Running three Qwen-guided dialogue/tool rounds..."):
        try:
            state = orchestrator.run_three_rounds(quick_goal)
            st.session_state.conversation_state = state
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
