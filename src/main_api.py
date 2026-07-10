"""FastAPI backend for Pure Qwen Shell."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from src.pure_qwen_client import PureQwenClient, pure_qwen_metadata
from src.pure_schemas import DebugPayloadResponse, PureChatRequest, PureChatResponse
from src.search_qwen_client import SEARCH_TOOLS, SearchQwenClient, search_qwen_metadata
from src.search_schemas import SearchChatRequest, SearchChatResponse, SearchDebugPayloadResponse


app = FastAPI(
    title="Pure Qwen Shell API",
    description="A minimal Qwen pass-through API with pure chat and optional native Qwen search.",
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


@app.get("/api/search_ping")
def search_ping() -> dict:
    """Direct Qwen search-mode connectivity check."""

    try:
        client = SearchQwenClient()
        model = client.model
        result = client.search(
            message="请联网搜索合肥市今天的天气，并说明信息是否来自实时搜索。",
            model=model,
        )
        return {
            "status": "ok",
            "mode": "qwen_search",
            "model": model,
            "response_id": result["response_id"],
            "request_id": result["request_id"],
            "search_used": result["search_used"],
            "sources": result["sources"],
            "tool_usage": result["tool_usage"],
            "reply": result["reply"],
        }
    except Exception as exc:  # noqa: BLE001 - endpoint is diagnostic by design.
        model = str(search_qwen_metadata()["model"])
        return {
            "status": "error",
            "mode": "qwen_search",
            "model": model,
            **_error_payload(exc),
            "hint": (
                "Responses API search failed. Check whether the selected model supports the "
                "web_search and web_extractor tools, the region/base_url, and account permissions."
            ),
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


@app.post("/api/debug_search_payload", response_model=SearchDebugPayloadResponse)
def debug_search_payload(request: SearchChatRequest) -> SearchDebugPayloadResponse:
    """Return the exact safe Responses API search payload without calling Qwen."""

    return SearchDebugPayloadResponse(
        model=request.model or str(search_qwen_metadata()["model"]),
        input=request.message,
        previous_response_id=request.previous_response_id,
        tools=[tool.copy() for tool in SEARCH_TOOLS],
    )


@app.post("/api/chat_search", response_model=SearchChatResponse)
def chat_search(request: SearchChatRequest) -> SearchChatResponse:
    """Send one user turn through Qwen's Responses API web tools."""

    try:
        client = SearchQwenClient()
        model = request.model or client.model
        result = client.search(
            message=request.message,
            model=model,
            previous_response_id=request.previous_response_id,
        )
    except Exception as exc:  # noqa: BLE001 - API should return an actionable error.
        detail = {
            **_error_payload(exc),
            "hint": (
                "Responses API search failed. Check whether the selected model supports the "
                "web_search and web_extractor tools, the region/base_url, and account permissions."
            ),
        }
        status_code = 500 if isinstance(exc, ValueError) else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return SearchChatResponse(
        reply=str(result["reply"]),
        model=model,
        response_id=result["response_id"],  # type: ignore[arg-type]
        request_id=result["request_id"],  # type: ignore[arg-type]
        search_used=bool(result["search_used"]),
        sources=result["sources"],  # type: ignore[arg-type]
        tool_usage=result["tool_usage"],  # type: ignore[arg-type]
    )


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
