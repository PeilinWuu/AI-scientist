"""Qwen Responses API client with built-in web tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from openai import BadRequestError
from openai import OpenAI

from src.model_utils import normalize_model_name


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
SEARCH_TOOLS = [
    {"type": "web_search"},
    {"type": "web_extractor"},
]


class SearchQwenClient:
    """Call Qwen's Responses API with built-in web tools."""

    def __init__(self, timeout_env: str | None = None) -> None:
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is missing. Please set it in .env.")

        self.default_model = os.getenv("LLM_SEARCH_MODEL", "qwen3.7-plus")
        self.model = self.default_model
        self.base_url = (
            os.getenv("RESPONSES_BASE_URL")
            or os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        if timeout_env:
            self.timeout = float(os.getenv(timeout_env, os.getenv("LLM_TIMEOUT", "120")))
        else:
            self.timeout = float(os.getenv("LLM_TIMEOUT", "120"))
        self.http_client = httpx.Client(
            timeout=self.timeout,
            trust_env=False,
        )
        self.client = OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            http_client=self.http_client,
        )

    def search(
        self,
        message: str,
        model: str | None = None,
        previous_response_id: str | None = None,
    ) -> dict[str, object]:
        """Send the current user text and optionally continue a Responses conversation."""

        resolved_model = normalize_model_name(model) or self.default_model
        tools = [tool.copy() for tool in SEARCH_TOOLS]
        validate_search_request(resolved_model, message, tools)
        kwargs: dict[str, object] = {
            "model": resolved_model,
            "input": message,
            "tools": tools,
        }
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id

        try:
            response = self.client.responses.create(**kwargs)
        except Exception as exc:
            _annotate_search_exception(
                exc,
                model=resolved_model,
                base_url=self.base_url,
                tools=tools,
                previous_response_id=previous_response_id,
            )
            raise
        return self.parse_response(response)

    def parse_response(self, response: Any) -> dict[str, object]:
        """Separate final assistant text from citations and tool status."""

        reply = extract_final_text(response)
        if not reply:
            raise RuntimeError("Qwen Responses API returned no final assistant output_text.")

        sources = extract_sources(response)
        tool_usage = extract_tool_usage(response)
        return {
            "reply": reply,
            "response_id": _text_value(response, "id"),
            "request_id": extract_request_id(response),
            "search_used": tool_usage["web_search"] > 0 or bool(sources),
            "sources": sources,
            "tool_usage": tool_usage,
        }


def extract_final_text(response: Any) -> str:
    """Return only final assistant output text from a Responses API result."""

    output_text = _value(response, "output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    texts: list[str] = []
    for item in _items(_value(response, "output")):
        if _value(item, "type") != "message":
            continue
        if _value(item, "role") not in (None, "assistant"):
            continue
        for content in _items(_value(item, "content")):
            if _value(content, "type") != "output_text":
                continue
            text = _value(content, "text")
            if isinstance(text, str) and text:
                texts.append(text)
    return "\n".join(texts).strip()


def extract_sources(response: Any) -> list[dict[str, object]]:
    """Extract explicit URL citations attached to assistant output text."""

    sources: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for content in _assistant_output_text_items(response):
        for annotation in _items(_value(content, "annotations")):
            citation = _value(annotation, "url_citation") or annotation
            url = _first_text(citation, ["url", "source_url", "link"])
            title = _first_text(citation, ["title", "name"])
            if not url:
                continue
            key = (url, title)
            if key in seen:
                continue
            seen.add(key)
            sources.append(
                {
                    "site_name": _first_text(citation, ["site_name", "site", "domain"]),
                    "title": title,
                    "url": url,
                    "snippet": _first_text(citation, ["snippet", "summary"]),
                    "index": len(sources) + 1,
                }
            )
    return sources


def extract_tool_usage(response: Any) -> dict[str, int]:
    """Count built-in tool calls without exposing their intermediate payloads."""

    counts = {"web_search": 0, "web_extractor": 0}
    for item in _items(_value(response, "output")):
        item_type = _value(item, "type")
        if item_type == "web_search_call":
            counts["web_search"] += 1
        elif item_type == "web_extractor_call":
            counts["web_extractor"] += 1

    x_tools = _value(_value(response, "usage"), "x_tools")
    for tool_name in counts:
        provider_count = _value(_value(x_tools, tool_name), "count")
        if isinstance(provider_count, int):
            counts[tool_name] = max(counts[tool_name], provider_count)
    return counts


def extract_request_id(response: Any) -> str | None:
    """Read an explicit provider request identifier when one is available."""

    for name in ("request_id", "_request_id"):
        value = _value(response, name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    model_extra = _value(response, "model_extra")
    value = _value(model_extra, "request_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def search_qwen_metadata() -> dict[str, object]:
    """Return public Responses API configuration without secrets."""

    base_url = os.getenv("RESPONSES_BASE_URL") or os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)
    return {
        "mode": "qwen_search",
        "model": os.getenv("LLM_SEARCH_MODEL", "qwen3.7-plus"),
        "base_url": base_url.rstrip("/"),
        "api_key_configured": bool(os.getenv("DASHSCOPE_API_KEY", "")),
        "search_method": "responses_api_builtin_tools",
        "tools": [tool.copy() for tool in SEARCH_TOOLS],
    }


def validate_search_request(model: str | None, input_text: str | None, tools: list[dict[str, str]]) -> None:
    """Validate a minimal Responses API search request before provider I/O."""

    normalized_model = normalize_model_name(model)
    if not normalized_model:
        raise ValueError("Search model must be a non-empty string.")
    if not isinstance(input_text, str) or not input_text.strip():
        raise ValueError("Search input must be a non-empty string.")
    tool_names = {tool.get("type") for tool in tools}
    if "web_search" not in tool_names:
        raise ValueError("Search tools must include web_search.")
    if "web_extractor" in tool_names and "web_search" not in tool_names:
        raise ValueError("web_extractor requires web_search.")


def _annotate_search_exception(
    exc: Exception,
    model: str,
    base_url: str,
    tools: list[dict[str, str]],
    previous_response_id: str | None,
) -> None:
    """Attach safe diagnostics to provider exceptions without wrapping them."""

    setattr(exc, "requested_model", model)
    setattr(exc, "actual_model", model)
    setattr(exc, "endpoint_host", urlparse(base_url).netloc)
    setattr(exc, "tool_names", [tool.get("type", "") for tool in tools])
    setattr(exc, "previous_response_id_present", bool(previous_response_id))
    if isinstance(exc, BadRequestError):
        setattr(exc, "status_code", getattr(exc, "status_code", 400))
    provider_body = getattr(exc, "body", None)
    if isinstance(provider_body, dict):
        error = provider_body.get("error") if isinstance(provider_body.get("error"), dict) else provider_body
        setattr(exc, "provider_error_code", str(error.get("code", "")))
        setattr(exc, "provider_error_message", str(error.get("message", "")))
    request_id = getattr(exc, "request_id", None) or getattr(exc, "_request_id", None)
    if request_id:
        setattr(exc, "request_id", str(request_id))


def _assistant_output_text_items(response: Any) -> list[Any]:
    items: list[Any] = []
    for output_item in _items(_value(response, "output")):
        if _value(output_item, "type") != "message":
            continue
        if _value(output_item, "role") not in (None, "assistant"):
            continue
        for content in _items(_value(output_item, "content")):
            if _value(content, "type") == "output_text":
                items.append(content)
    return items


def _value(data: Any, name: str) -> Any:
    if data is None:
        return None
    if isinstance(data, dict):
        return data.get(name)
    return getattr(data, name, None)


def _text_value(data: Any, name: str) -> str | None:
    value = _value(data, name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _items(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _first_text(data: Any, names: list[str]) -> str:
    for name in names:
        value = _value(data, name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
