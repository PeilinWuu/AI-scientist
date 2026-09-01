"""Bounded Responses API tools used by Evidence Researcher."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from src.ai_scientist.schemas import SearchCandidate, utc_now
from src.search_qwen_client import (
    SearchQwenClient,
    extract_final_text,
    extract_request_id,
    extract_sources as extract_response_sources,
)


class QwenEvidenceSearchTool:
    name = "web_search"

    def search_query(self, query: str, model: str) -> dict[str, object]:
        """Run one independent query with web_search as the only tool."""

        client = SearchQwenClient(timeout_env="AI_SCIENTIST_SEARCH_QUERY_TIMEOUT")
        try:
            response = client.client.responses.create(
                model=model,
                input=query,
                tools=[{"type": "web_search"}],
            )
        except Exception as exc:
            _annotate(exc, model, client.base_url, ["web_search"])
            raise
        candidates = _search_candidates(response, query)
        return {
            "candidates": [item.model_dump(mode="json") for item in candidates],
            "response_id": _value(response, "id"),
            "request_id": extract_request_id(response),
            "final_text": extract_final_text(response),
        }

    def extract_sources(
        self,
        candidates: list[SearchCandidate],
        model: str,
        progress_callback: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        """Extract only the supplied URLs using a streaming Responses request."""

        if not candidates:
            return {"final_text": "", "response_id": None, "request_id": None}
        client = SearchQwenClient(timeout_env="AI_SCIENTIST_EXTRACTION_TIMEOUT")
        source_lines = [f"- {item.title or '(untitled)'}: {item.url}" for item in candidates]
        prompt = "\n".join(
            [
                "Only analyze the candidate sources listed below. Do not perform a new broad topic search.",
                "Extract bibliographic metadata, study design, population, interventions, outcomes, findings, and limitations.",
                "Keep every statement tied to its source URL.",
                *source_lines,
            ]
        )
        tools = [{"type": "web_search"}, {"type": "web_extractor"}]
        try:
            stream = client.client.responses.create(
                model=model,
                input=prompt,
                tools=tools,
                stream=True,
            )
            completed_response = _consume_stream(stream, progress_callback)
        except Exception as exc:
            _annotate(exc, model, client.base_url, [item["type"] for item in tools])
            raise
        if completed_response is None:
            raise RuntimeError("Source extraction stream ended without response.completed.")
        return {
            "final_text": extract_final_text(completed_response),
            "response_id": _value(completed_response, "id"),
            "request_id": extract_request_id(completed_response),
        }

    def run(
        self,
        query: str,
        model: str,
        previous_response_id: str | None = None,
    ) -> dict[str, object]:
        """Compatibility wrapper for old callers; never carries prior context."""

        client = SearchQwenClient(timeout_env="AI_SCIENTIST_SEARCH_QUERY_TIMEOUT")
        if not hasattr(client, "client"):
            return client.search(
                message=query,
                model=model,
                previous_response_id=previous_response_id,
            )
        result = self.search_query(query, model)
        candidates = [SearchCandidate.model_validate(item) for item in result["candidates"]]
        return {
            "reply": result.get("final_text", ""),
            "sources": [
                {"title": item.title, "url": item.url, "site_name": item.source_domain, "snippet": item.snippet}
                for item in candidates
            ],
            "response_id": result.get("response_id"),
            "request_id": result.get("request_id"),
            "search_used": bool(candidates),
        }


def _search_candidates(response: Any, query: str) -> list[SearchCandidate]:
    candidates: list[SearchCandidate] = []
    seen: set[str] = set()

    def append_source(source: Any) -> None:
        url = _text(source, ["url", "source_url", "link"])
        if not url or url in seen:
            return
        seen.add(url)
        candidates.append(
            SearchCandidate(
                title=_text(source, ["title", "name"]),
                url=url,
                query=query,
                rank=len(candidates) + 1,
                source_domain=(
                    _text(source, ["site_name", "site", "domain"])
                    or urlparse(url).netloc.lower()
                ),
                snippet=_text(source, ["snippet", "summary", "text"]),
                discovered_at=utc_now(),
            )
        )

    for item in _items(_value(response, "output")):
        if _value(item, "type") != "web_search_call":
            continue
        action = _value(item, "action")
        for source in _items(_value(action, "sources")):
            append_source(source)
    # Bailian Responses may expose citations on assistant output annotations
    # instead of web_search_call.action.sources. Both are provider-owned,
    # explicit source metadata and are safe to normalize as candidates.
    for source in extract_response_sources(response):
        append_source(source)
    return candidates


def _consume_stream(stream: Any, callback: Callable[[str], None] | None) -> Any:
    completed_response = None
    for event in stream:
        event_type = str(_value(event, "type") or "")
        if event_type in {
            "response.created",
            "response.output_item.added",
            "response.output_item.done",
            "response.completed",
            "response.failed",
        } and callback:
            callback(event_type)
        if event_type == "response.completed":
            completed_response = _value(event, "response")
        elif event_type == "response.failed":
            response = _value(event, "response")
            error = _value(response, "error") or _value(event, "error")
            raise RuntimeError(f"Source extraction failed: {_value(error, 'message') or 'provider failure'}")
    return completed_response


def _annotate(exc: Exception, model: str, base_url: str, tools: list[str]) -> None:
    setattr(exc, "requested_model", model)
    setattr(exc, "actual_model", model)
    setattr(exc, "endpoint_host", urlparse(base_url).netloc)
    setattr(exc, "tool_names", tools)
    setattr(exc, "previous_response_id_present", False)


def _value(value: Any, name: str) -> Any:
    if value is None:
        return None
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _items(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: Any, names: list[str]) -> str:
    for name in names:
        candidate = _value(value, name)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""
