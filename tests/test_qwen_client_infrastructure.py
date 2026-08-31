"""Regression tests for Qwen and Responses-search infrastructure used by AI Scientist."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.pure_qwen_client import PureQwenClient
from src.search_qwen_client import (
    SEARCH_TOOLS,
    SearchQwenClient,
    extract_final_text,
    extract_sources,
    extract_tool_usage,
    resolve_search_tools,
)


def test_shared_qwen_client_uses_no_proxy_httpx_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("LLM_TIMEOUT", "60")
    client = PureQwenClient()
    assert getattr(client.http_client, "_trust_env") is False
    assert client.timeout == 60.0


def test_search_sends_original_input_and_previous_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    client = SearchQwenClient()
    captured: dict[str, object] = {}

    class FakeResponses:
        def create(self, **kwargs: object) -> object:
            captured.update(kwargs)
            return SimpleNamespace(
                id="resp_123",
                request_id="request_123",
                output_text="干净的最终回答",
                output=[SimpleNamespace(type="web_search_call")],
                usage=None,
            )

    client.client = SimpleNamespace(responses=FakeResponses())
    user_text = "搜索世界杯最新一场比赛结果。"
    result = client.search(user_text, model="qwen3.7-plus", previous_response_id="resp_previous")

    assert captured == {
        "model": "qwen3.7-plus",
        "input": user_text,
        "tools": client.tools,
        "previous_response_id": "resp_previous",
    }
    assert result["reply"] == "干净的最终回答"
    assert "raw_response" not in result


def test_non_dashscope_gateway_uses_web_search_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QWEN_SEARCH_ENABLE_WEB_EXTRACTOR", raising=False)
    assert resolve_search_tools("https://api.llm.ustc.edu.cn/v1") == [{"type": "web_search"}]


def test_dashscope_gateway_keeps_web_extractor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QWEN_SEARCH_ENABLE_WEB_EXTRACTOR", raising=False)
    assert resolve_search_tools("https://dashscope.aliyuncs.com/compatible-mode/v1") == SEARCH_TOOLS


def test_output_text_has_priority_over_all_output_items() -> None:
    response = SimpleNamespace(
        output_text="最终回答",
        output=[
            SimpleNamespace(type="reasoning", content="internal reasoning"),
            SimpleNamespace(type="web_search_call", action=SimpleNamespace(query="private query")),
            SimpleNamespace(
                type="message",
                role="assistant",
                content=[SimpleNamespace(type="output_text", text="fallback text")],
            ),
        ],
    )
    assert extract_final_text(response) == "最终回答"


def test_fallback_extracts_only_assistant_output_text() -> None:
    response = SimpleNamespace(
        output_text=None,
        output=[
            SimpleNamespace(type="reasoning", content="must stay hidden"),
            SimpleNamespace(type="web_search_call", result="raw retrieved text"),
            SimpleNamespace(
                type="message",
                role="user",
                content=[SimpleNamespace(type="output_text", text="user text")],
            ),
            SimpleNamespace(
                type="message",
                role="assistant",
                content=[
                    SimpleNamespace(type="input_text", text="hidden input"),
                    SimpleNamespace(type="output_text", text="assistant answer"),
                ],
            ),
        ],
    )
    assert extract_final_text(response) == "assistant answer"


def test_sources_and_tool_usage_are_separate_from_reply() -> None:
    annotation = SimpleNamespace(
        type="url_citation", url="https://example.com/result", title="Example result"
    )
    response = SimpleNamespace(
        output_text="Answer [1]",
        output=[
            SimpleNamespace(type="web_search_call"),
            SimpleNamespace(type="web_extractor_call"),
            SimpleNamespace(
                type="message",
                role="assistant",
                content=[SimpleNamespace(type="output_text", text="Answer [1]", annotations=[annotation])],
            ),
        ],
        usage=None,
    )
    assert extract_sources(response) == [{
        "site_name": "",
        "title": "Example result",
        "url": "https://example.com/result",
        "snippet": "",
        "index": 1,
    }]
    assert extract_tool_usage(response) == {"web_search": 1, "web_extractor": 1}
    assert extract_final_text(response) == "Answer [1]"


def test_search_production_source_has_no_manual_prompt_fragments() -> None:
    production_files = [Path("app_streamlit.py"), *Path("src").rglob("*.py")]
    source = "\n".join(path.read_text(encoding="utf-8") for path in production_files)
    forbidden = [
        "当前应用层已经为本次请求启用了互联网搜索",
        "来自你的知识库的内容",
        "来自系统的内容",
        "请在回答时引用上述内容",
        "{" + "system_time" + "}",
    ]
    for fragment in forbidden:
        assert fragment not in source


def test_shared_qwen_client_does_not_use_search_parameters() -> None:
    source = Path("src/pure_qwen_client.py").read_text(encoding="utf-8")
    for forbidden in ["extra_body", "enable_search", "search_options", "temperature", "top_p"]:
        assert forbidden not in source


def test_search_client_uses_responses_tools_without_messages() -> None:
    source = Path("src/search_qwen_client.py").read_text(encoding="utf-8")
    assert "client.responses.create" in source
    assert '"input": message' in source
    assert '"previous_response_id"' in source
    assert '"type": "web_search"' in source
    assert '"type": "web_extractor"' in source
    assert '"role": "system"' not in source
    assert "chat.completions.create" not in source

