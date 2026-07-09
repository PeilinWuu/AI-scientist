"""FastAPI backend for Pure Qwen Shell."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from src.pure_qwen_client import PureQwenClient, pure_qwen_metadata
from src.pure_schemas import DebugPayloadResponse, PureChatRequest, PureChatResponse


app = FastAPI(
    title="Pure Qwen Shell API",
    description="A minimal Qwen pass-through API with no hidden agent, tools, search, or system prompt.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict:
    """Return public backend status."""

    return {"status": "ok", **pure_qwen_metadata()}


@app.post("/api/debug_payload", response_model=DebugPayloadResponse)
def debug_payload(request: PureChatRequest) -> DebugPayloadResponse:
    """Return the exact messages that would be sent to Qwen without calling Qwen."""

    messages = _build_messages_without_client(request)
    return DebugPayloadResponse(
        messages=messages,
        model=request.model or str(pure_qwen_metadata()["model"]),
    )


@app.get("/api/qwen_ping")
def qwen_ping() -> dict:
    """Direct Qwen connectivity check using the same client as /api/chat."""

    model = "qwen-turbo"
    try:
        client = PureQwenClient()
        reply = client.chat(messages=[{"role": "user", "content": "ping"}], model=model)
        return {
            "status": "ok",
            "mode": "pure_qwen",
            "model": model,
            "reply": reply,
        }
    except Exception as exc:  # noqa: BLE001 - endpoint is diagnostic by design.
        return {
            "status": "error",
            "mode": "pure_qwen",
            **_error_payload(exc),
        }


@app.post("/api/chat", response_model=PureChatResponse)
def chat(request: PureChatRequest) -> PureChatResponse:
    """Send a pure user/assistant message list to Qwen."""

    try:
        client = PureQwenClient()
        messages = client.build_messages(request.message, request.history)
        model = request.model or client.model
        reply = client.chat(messages, model=model)
    except Exception as exc:  # noqa: BLE001 - API should return an actionable error.
        detail = {
            **_error_payload(exc),
            "hint": (
                "PureQwenClient uses OpenAI(api_key, base_url, http_client=httpx.Client("
                "timeout=LLM_TIMEOUT, trust_env=False)). If /api/qwen_ping fails, restart uvicorn "
                "after checking DASHSCOPE_API_KEY, LLM_BASE_URL, and LLM_MODEL in .env."
            ),
        }
        status_code = 500 if isinstance(exc, ValueError) else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return PureChatResponse(reply=reply, model=model)


def _build_messages_without_client(request: PureChatRequest) -> list[dict[str, str]]:
    return [
        {"role": item.role, "content": item.content}
        for item in request.history
        if item.role in {"user", "assistant"}
    ] + [{"role": "user", "content": request.message}]


def _error_payload(exc: Exception) -> dict[str, str]:
    cause = exc.__cause__ or exc.__context__
    return {
        "error_type": type(exc).__name__,
        "error_message": _sanitize(str(exc)),
        "cause_type": type(cause).__name__ if cause else "",
        "cause_message": _sanitize(str(cause)) if cause else "",
    }


def _sanitize(message: str) -> str:
    api_key_configured = pure_qwen_metadata().get("api_key_configured")
    if not api_key_configured:
        return message
    try:
        import os

        api_key = os.getenv("DASHSCOPE_API_KEY", "")
    except Exception:  # noqa: BLE001 - sanitization must never mask the original error.
        api_key = ""
    return message.replace(api_key, "[REDACTED_API_KEY]") if api_key else message
