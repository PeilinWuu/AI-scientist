"""Tests for Pure Qwen Shell payload construction."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.main_api import app
from src.pure_qwen_client import PureQwenClient
from src.pure_schemas import ChatMessage
from src.search_qwen_client import SearchQwenClient, detect_search_effective, extract_sources, search_extra_body
from src.search_schemas import ChatMessage as SearchChatMessage


def test_build_messages_has_no_system_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    client = PureQwenClient()

    messages = client.build_messages(
        "hello",
        [ChatMessage(role="user", content="previous"), ChatMessage(role="assistant", content="reply")],
    )

    assert messages == [
        {"role": "user", "content": "previous"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "hello"},
    ]
    assert all(item["role"] != "system" for item in messages)


def test_build_messages_preserves_user_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    client = PureQwenClient()
    text = "你是谁？"

    messages = client.build_messages(text, [])

    assert messages[-1] == {"role": "user", "content": text}


def test_pure_client_uses_no_proxy_httpx_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("LLM_TIMEOUT", "60")

    client = PureQwenClient()

    assert getattr(client.http_client, "_trust_env") is False
    assert client.timeout == 60.0


def test_history_allows_only_user_and_assistant() -> None:
    with pytest.raises(ValidationError):
        ChatMessage(role="system", content="hidden")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ChatMessage(role="tool", content="hidden")  # type: ignore[arg-type]


def test_debug_payload_has_only_visible_messages() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/debug_payload",
        json={"message": "你是谁？", "history": [], "model": "qwen-turbo"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "pure_qwen"
    assert payload["model"] == "qwen-turbo"
    assert payload["messages"] == [{"role": "user", "content": "你是谁？"}]

    payload_text = str(payload["messages"])
    forbidden_terms = [
        "Flow" + "Scientist",
        "soft" + "-swimmer",
        "实验规划",
        "strict JSON",
        "to" + "ol",
        "ski" + "ll",
    ]
    for forbidden in forbidden_terms:
        assert forbidden not in payload_text


def test_debug_payload_rejects_system_history() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/debug_payload",
        json={
            "message": "hello",
            "history": [{"role": "system", "content": "hidden"}],
            "model": "qwen-turbo",
        },
    )

    assert response.status_code == 422


def test_debug_search_payload_has_enable_search() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/debug_search_payload",
        json={"message": "今天杭州天气怎么样？", "history": [], "model": "qwen-turbo"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "qwen_search"
    assert payload["model"] == "qwen-turbo"
    assert payload["messages"][0]["role"] == "system"
    assert "互联网搜索" in payload["messages"][0]["content"]
    assert payload["messages"][-1] == {"role": "user", "content": "今天杭州天气怎么样？"}
    assert payload["extra_body"] == {
        "enable_search": True,
        "search_options": {
            "forced_search": True,
            "enable_source": True,
            "enable_citation": True,
            "citation_format": "[<number>]",
            "search_strategy": "turbo",
        },
    }


def test_search_history_allows_only_user_and_assistant() -> None:
    with pytest.raises(ValidationError):
        SearchChatMessage(role="system", content="hidden")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        SearchChatMessage(role="tool", content="hidden")  # type: ignore[arg-type]


def test_search_client_build_messages_preserves_user_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("QWEN_SEARCH_STRATEGY", "turbo")
    client = SearchQwenClient()
    text = "今天合肥天气怎么样？"

    messages = client.build_messages(text, [])

    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": text}
    assert search_extra_body() == {
        "enable_search": True,
        "search_options": {
            "forced_search": True,
            "enable_source": True,
            "enable_citation": True,
            "citation_format": "[<number>]",
            "search_strategy": "turbo",
        },
    }


def test_search_chat_response_metadata() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/debug_search_payload",
        json={"message": "weather", "history": [], "model": "qwen-turbo"},
    )
    payload = response.json()

    assert payload["mode"] == "qwen_search"
    assert payload["extra_body"]["search_options"]["forced_search"] is True
    assert payload["extra_body"]["search_options"]["enable_source"] is True
    assert payload["extra_body"]["search_options"]["enable_citation"] is True


def test_extract_sources_and_detect_search_effective() -> None:
    raw_response = {
        "id": "request-123",
        "search_info": {
            "search_results": [
                {
                    "site_name": "Example",
                    "title": "Weather result",
                    "url": "https://example.com/weather",
                    "snippet": "Current weather.",
                }
            ]
        },
    }

    sources = extract_sources(raw_response)

    assert sources == [
        {
            "site_name": "Example",
            "title": "Weather result",
            "url": "https://example.com/weather",
            "snippet": "Current weather.",
            "index": 1,
        }
    ]
    assert detect_search_effective("answer", sources) == (True, None)


def test_detect_search_failure_without_sources() -> None:
    effective, warning = detect_search_effective("根据我现有的知识库，我无法联网。", [])

    assert effective is False
    assert warning == "Search was requested, but the response indicates that web search was not effectively used."


def test_main_api_does_not_import_removed_chain() -> None:
    source = Path("src/main_api.py").read_text(encoding="utf-8")

    forbidden_imports = [
        "Dialogue" + "Orchestrator",
        "Intent" + "Router",
        "Qwen" + "Provider",
        "Curl" + "Qwen" + "Provider",
        "get_default_" + "tools",
        "get_" + "skill" + "_prompt",
        "src." + "tools",
        "src." + "skills",
    ]
    for forbidden in forbidden_imports:
        assert forbidden not in source


def test_pure_client_does_not_use_search_parameters() -> None:
    source = Path("src/pure_qwen_client.py").read_text(encoding="utf-8")

    for forbidden in ["extra_body", "enable_search", "search_options", "temperature", "top_p"]:
        assert forbidden not in source


def test_search_client_uses_search_extra_body_only() -> None:
    source = Path("src/search_qwen_client.py").read_text(encoding="utf-8")

    assert "extra_body" in source
    assert "enable_search" in source
    assert "forced_search" in source
    assert "enable_source" in source
    assert "enable_citation" in source
    assert "search_strategy" in source
    for forbidden in ["temperature", "top_p", "response_format"]:
        assert forbidden not in source
