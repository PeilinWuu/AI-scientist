"""Streamlit frontend for Pure Qwen Shell."""

from __future__ import annotations

import requests
import streamlit as st


DEFAULT_BACKEND_URL = "http://localhost:8000"
MODEL_OPTIONS = ["qwen-turbo", "qwen-plus", "qwen-max"]


def chat_history() -> list[dict[str, str]]:
    """Return only user/assistant messages for the backend payload."""

    return [
        {"role": item["role"], "content": item["content"]}
        for item in st.session_state.messages
        if item.get("role") in {"user", "assistant"}
    ]


def post_json(backend_url: str, path: str, payload: dict) -> dict:
    """POST JSON to the Pure Qwen Shell backend."""

    url = f"{backend_url.rstrip('/')}{path}"
    response = requests.post(url, json=payload, timeout=120)
    if not response.ok:
        raise BackendAPIError(response.status_code, _extract_error_detail(response))
    return response.json()


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

with st.sidebar:
    st.header("Pure Qwen Shell")
    backend_url = st.text_input("Backend URL", value=DEFAULT_BACKEND_URL)
    model = st.selectbox("Model", MODEL_OPTIONS, index=0)
    show_debug = st.checkbox("Developer debug", value=False)
    if st.button("Clear conversation"):
        st.session_state.messages = []
        st.session_state.last_debug_payload = None
        st.rerun()
    if st.button("查看发送 payload"):
        if st.session_state.last_debug_payload is None:
            st.info("还没有可查看的 payload。请先发送一条消息。")
        else:
            st.json(st.session_state.last_debug_payload)

st.title("Pure Qwen Shell")
st.caption("A minimal chat UI that sends only user/assistant messages to Qwen.")

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])

user_input = st.chat_input("请输入消息")
if user_input:
    history = chat_history()
    payload = {
        "message": user_input,
        "history": history,
        "model": model,
    }

    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.write(user_input)

    try:
        debug_payload = post_json(backend_url, "/api/debug_payload", payload)
        st.session_state.last_debug_payload = debug_payload

        with st.spinner("Qwen is replying..."):
            response = post_json(backend_url, "/api/chat", payload)
        reply = response.get("reply", "")
        st.session_state.messages.append({"role": "assistant", "content": reply})
        with st.chat_message("assistant"):
            st.write(reply)
        if show_debug:
            with st.expander("Debug payload sent to Qwen", expanded=False):
                st.json(debug_payload)
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
