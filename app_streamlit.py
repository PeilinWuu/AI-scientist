"""Streamlit frontend for Pure Qwen Shell with optional search mode."""

from __future__ import annotations

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


def chat_history() -> list[dict[str, str]]:
    """Return only user/assistant messages for the backend payload."""

    return [
        {"role": item["role"], "content": item["content"]}
        for item in st.session_state.messages
        if item.get("role") in {"user", "assistant"}
    ]


def post_json(backend_url: str, path: str, payload: dict) -> dict:
    """POST JSON to the selected backend endpoint."""

    url = f"{backend_url.rstrip('/')}{path}"
    response = requests.post(url, json=payload, timeout=120)
    if not response.ok:
        raise BackendAPIError(response.status_code, _extract_error_detail(response))
    return response.json()


def render_search_debug(metadata: dict) -> None:
    """Render search diagnostics outside the assistant chat message."""

    with st.expander("搜索来源", expanded=bool(metadata.get("sources"))):
        sources = metadata.get("sources") or []
        if sources:
            for source in sources:
                index = source.get("index", "")
                title = source.get("title") or "(untitled)"
                site_name = source.get("site_name") or "(unknown site)"
                url = source.get("url") or ""
                snippet = source.get("snippet") or ""
                st.markdown(f"**[{index}] {title}**")
                st.write(site_name)
                if url:
                    st.write(url)
                if snippet:
                    st.caption(snippet)
        else:
            st.info("当前接口未返回可验证搜索来源。")
    with st.expander("Response metadata", expanded=False):
        st.json(metadata)


class BackendAPIError(RuntimeError):
    """Error wrapper that preserves the backend JSON detail."""

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


st.set_page_config(page_title="Pure Qwen Shell", layout="wide")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_debug_payload" not in st.session_state:
    st.session_state.last_debug_payload = None
if "last_endpoint" not in st.session_state:
    st.session_state.last_endpoint = None
if "last_chat_endpoint" not in st.session_state:
    st.session_state.last_chat_endpoint = None
if "last_response_metadata" not in st.session_state:
    st.session_state.last_response_metadata = None
if "search_previous_response_id" not in st.session_state:
    st.session_state.search_previous_response_id = None
if "last_search_model" not in st.session_state:
    st.session_state.last_search_model = None

with st.sidebar:
    st.header("Pure Qwen Shell")
    backend_url = st.text_input("Backend URL", value=DEFAULT_BACKEND_URL)
    search_enabled = st.toggle("启用联网搜索", value=False)
    if search_enabled:
        default_model = os.getenv("LLM_SEARCH_MODEL", "qwen3.7-plus")
        default_index = (
            SEARCH_MODEL_OPTIONS.index(default_model) if default_model in SEARCH_MODEL_OPTIONS else 0
        )
        model = st.selectbox(
            "Model", SEARCH_MODEL_OPTIONS, index=default_index, key="search_model_control"
        )
    else:
        default_model = os.getenv("LLM_MODEL", "qwen-turbo")
        default_index = PURE_MODEL_OPTIONS.index(default_model) if default_model in PURE_MODEL_OPTIONS else 0
        model = st.selectbox("Model", PURE_MODEL_OPTIONS, index=default_index, key="pure_model_control")
    st.write(f"**Current mode:** {'Qwen Search' if search_enabled else 'Pure Qwen'}")
    show_debug = st.checkbox("Developer debug", value=False)
    if st.button("Clear conversation"):
        st.session_state.messages = []
        st.session_state.search_previous_response_id = None
        st.session_state.last_search_model = None
        st.session_state.last_debug_payload = None
        st.session_state.last_endpoint = None
        st.session_state.last_chat_endpoint = None
        st.session_state.last_response_metadata = None
        st.rerun()
    if show_debug and st.button("查看发送 payload"):
        if st.session_state.last_debug_payload is None:
            st.info("还没有可查看的 payload。请先发送一条消息。")
        else:
            st.write(f"**Chat endpoint:** `{st.session_state.last_chat_endpoint}`")
            st.write(f"**Debug endpoint:** `{st.session_state.last_endpoint}`")
            st.json(st.session_state.last_debug_payload)
            if st.session_state.last_response_metadata:
                st.write("**Last response metadata:**")
                st.json(st.session_state.last_response_metadata)

if not search_enabled:
    st.session_state.search_previous_response_id = None
    st.session_state.last_search_model = None
elif st.session_state.last_search_model not in (None, model):
    st.session_state.search_previous_response_id = None
if search_enabled:
    st.session_state.last_search_model = model

st.title("Pure Qwen Shell")
st.caption("与 Qwen 对话，可在侧边栏选择是否启用联网搜索。")

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])

user_input = st.chat_input("请输入消息")
if user_input:
    history = chat_history()
    if search_enabled:
        payload = {
            "message": user_input,
            "model": model,
            "previous_response_id": st.session_state.search_previous_response_id,
        }
    else:
        payload = {
            "message": user_input,
            "history": history,
            "model": model,
        }
    chat_endpoint = "/api/chat_search" if search_enabled else "/api/chat"
    debug_endpoint = "/api/debug_search_payload" if search_enabled else "/api/debug_payload"

    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.write(user_input)

    try:
        debug_payload = None
        if show_debug:
            debug_payload = post_json(backend_url, debug_endpoint, payload)
        st.session_state.last_debug_payload = debug_payload
        st.session_state.last_endpoint = debug_endpoint if show_debug else None
        st.session_state.last_chat_endpoint = chat_endpoint

        with st.spinner("Qwen is replying..."):
            response = post_json(backend_url, chat_endpoint, payload)
        st.session_state.last_response_metadata = {
            key: response.get(key)
            for key in [
                "mode",
                "model",
                "response_id",
                "request_id",
                "search_used",
                "sources",
                "tool_usage",
            ]
            if key in response
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
                if st.session_state.last_response_metadata:
                    st.write("**Response metadata:**")
                    st.json(st.session_state.last_response_metadata)
            if response.get("mode") == "qwen_search":
                render_search_debug(st.session_state.last_response_metadata or {})
    except BackendAPIError as exc:
        st.error("后端 Qwen 调用失败，错误详情如下。")
        if isinstance(exc.detail, dict):
            st.json(exc.detail)
        else:
            st.write(exc.detail)
    except Exception as exc:  # noqa: BLE001 - show UI error without writing it into chat history.
        st.error("前端或后端调用出错，请查看调试信息。")
        if show_debug:
            st.exception(exc)
