"""OpenAI-compatible Qwen client for optional search mode."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from openai import OpenAI

from src.search_schemas import ChatMessage


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
SEARCH_SYSTEM_MESSAGE = (
    "当前应用层已经为本次请求启用了互联网搜索。请基于搜索结果回答用户问题。"
    "不要声称自己无法联网、无法实时搜索或只能根据知识库回答。"
    "如果搜索结果不足或未返回来源，请明确说明搜索结果不足，而不是编造实时信息。"
)
SEARCH_METHOD = "chat_completions_enable_search_forced"
SEARCH_FAILURE_PHRASES = [
    "无法联网",
    "不能联网",
    "无法实时搜索",
    "无法实时获取",
    "无法直接访问互联网",
    "根据我现有的知识库",
    "根据我的知识库",
    "无法访问互联网",
    "不能访问互联网",
]


def search_strategy() -> str:
    """Return the configured Qwen search strategy."""

    strategy = os.getenv("QWEN_SEARCH_STRATEGY", "turbo").strip() or "turbo"
    return strategy if strategy in {"turbo", "max"} else "turbo"


def search_extra_body() -> dict:
    """Return the search extra_body sent to Qwen."""

    return {
        "enable_search": True,
        "search_options": {
            "forced_search": True,
            "enable_source": True,
            "enable_citation": True,
            "citation_format": "[<number>]",
            "search_strategy": search_strategy(),
        },
    }


def build_search_messages(message: str, history: list[ChatMessage]) -> list[dict[str, str]]:
    """Build search-mode messages with a minimal search-only system instruction."""

    messages: list[dict[str, str]] = [{"role": "system", "content": SEARCH_SYSTEM_MESSAGE}]
    messages.extend(
        {"role": item.role, "content": item.content}
        for item in history
        if item.role in {"user", "assistant"}
    )
    messages.append({"role": "user", "content": message})
    return messages


def detect_search_effective(reply: str, sources: list[dict[str, object]]) -> tuple[bool | None, str | None]:
    """Judge whether forced search appears effective without trusting model self-claims."""

    if sources:
        return True, None
    if _contains_failure_phrase(reply):
        return False, "Search was requested, but the response indicates that web search was not effectively used."
    return None, "Search was requested, but no source metadata was returned. The answer may not be verifiable."


def remove_bad_leading_disclaimer(reply: str, sources: list[dict[str, object]]) -> str:
    """Remove contradictory leading disclaimers only when real source metadata exists."""

    if not sources:
        return reply
    text = reply.lstrip()
    for _ in range(3):
        stripped = _strip_one_bad_leading_sentence(text)
        if stripped == text:
            break
        text = stripped.lstrip()
    return text or reply


class SearchQwenClient:
    """Call Qwen with native search enabled, without adding hidden prompts."""

    def __init__(self) -> None:
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY is missing. Please set it in .env.")

        self.api_key = api_key
        self.model = os.getenv("LLM_SEARCH_MODEL", "qwen-plus-latest")
        self.base_url = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(os.getenv("LLM_TIMEOUT", "60"))
        self.http_client = httpx.Client(
            timeout=self.timeout,
            trust_env=False,
        )
        self.client = OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            http_client=self.http_client,
        )

    def build_messages(self, message: str, history: list[ChatMessage]) -> list[dict[str, str]]:
        """Build the exact Qwen search-mode message list."""

        return build_search_messages(message, history)

    def chat_search(self, messages: list[dict[str, str]], model: str | None = None) -> dict[str, object]:
        """Send model, messages, and forced search settings to Qwen."""

        resolved_model = model or self.model
        extra_body = search_extra_body()
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=messages,
            extra_body=extra_body,
        )
        raw_response = _to_plain_data(response)
        reply = response.choices[0].message.content or ""
        sources = extract_sources(raw_response)
        cleaned_reply = remove_bad_leading_disclaimer(reply, sources)
        search_effective, warning = detect_search_effective(cleaned_reply, sources)
        return {
            "reply": cleaned_reply,
            "sources": sources,
            "search_effective": search_effective,
            "source_metadata_available": bool(sources),
            "warning": warning,
            "request_id": _extract_request_id(raw_response),
            "raw_response": raw_response,
        }


def search_qwen_metadata() -> dict[str, object]:
    """Return public search-mode metadata without exposing secrets."""

    return {
        "mode": "qwen_search",
        "model": os.getenv("LLM_SEARCH_MODEL", "qwen-plus-latest"),
        "base_url": os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        "api_key_configured": bool(os.getenv("DASHSCOPE_API_KEY", "")),
        "search_enabled": True,
        "search_forced": True,
        "search_strategy": search_strategy(),
        "search_method": SEARCH_METHOD,
    }


def extract_sources(raw_response: Any) -> list[dict[str, object]]:
    """Extract source metadata from known DashScope/OpenAI-compatible response fields."""

    raw_sources: list[Any] = []
    for key in ["search_info", "search_results", "citations", "references", "source", "sources"]:
        raw_sources.extend(_find_values_by_key(raw_response, key))

    normalized: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw_sources:
        candidates = item if isinstance(item, list) else [item]
        for candidate in candidates:
            source = _normalize_source(candidate, len(normalized) + 1)
            if source is None:
                continue
            dedupe_key = (
                str(source.get("url", "")),
                str(source.get("title", "")),
                str(source.get("snippet", ""))[:80],
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append(source)
    return normalized


def _normalize_source(item: Any, index: int) -> dict[str, object] | None:
    if not isinstance(item, dict):
        return None
    title = _first_text(item, ["title", "name", "text_title"])
    url = _first_text(item, ["url", "link", "source_url", "href"])
    snippet = _first_text(item, ["snippet", "summary", "content", "text", "description"])
    site_name = _first_text(item, ["site_name", "site", "source", "hostname", "domain"])
    if not any([title, url, snippet, site_name]):
        return None
    return {
        "site_name": site_name,
        "title": title,
        "url": url,
        "snippet": snippet,
        "index": index,
    }


def _first_text(data: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def _find_values_by_key(data: Any, key: str) -> list[Any]:
    values: list[Any] = []
    if isinstance(data, dict):
        for item_key, item_value in data.items():
            if item_key == key:
                values.append(item_value)
            values.extend(_find_values_by_key(item_value, key))
    elif isinstance(data, list):
        for item in data:
            values.extend(_find_values_by_key(item, key))
    return values


def _to_plain_data(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if hasattr(response, "dict"):
        return response.dict()
    return dict(response)


def _extract_request_id(raw_response: dict[str, Any]) -> str | None:
    for key in ["request_id", "requestId", "id"]:
        value = raw_response.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _contains_failure_phrase(reply: str) -> bool:
    return any(phrase in reply for phrase in SEARCH_FAILURE_PHRASES)


def _strip_one_bad_leading_sentence(reply: str) -> str:
    if not _contains_failure_phrase(reply[:120]):
        return reply
    match = re.match(r"^(.{0,160}?[。！？!?\n])", reply, flags=re.DOTALL)
    if match and _contains_failure_phrase(match.group(1)):
        return reply[match.end() :]
    for phrase in SEARCH_FAILURE_PHRASES:
        if reply.startswith(phrase):
            return reply[len(phrase) :].lstrip("，,。:： \n")
    return reply
