"""Tests for Pure Qwen Shell payload construction."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.main_api import app
from src.pure_qwen_client import PureQwenClient
from src.pure_schemas import ChatMessage


def test_build_messages_has_no_system_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    client = PureQwenClient()

    messages = client.build_messages(
        "你好",
        [ChatMessage(role="user", content="上一句"), ChatMessage(role="assistant", content="上一答")],
    )

    assert messages == [
        {"role": "user", "content": "上一句"},
        {"role": "assistant", "content": "上一答"},
        {"role": "user", "content": "你好"},
    ]
    assert all(item["role"] != "system" for item in messages)


def test_build_messages_preserves_user_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    client = PureQwenClient()
    text = "你是谁？请原样回答这句话里的标点。"

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


def test_debug_payload_has_no_hidden_agent_terms() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/debug_payload",
        json={"message": "你是谁？", "history": [], "model": "qwen-plus"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "pure_qwen"
    assert payload["model"] == "qwen-plus"
    assert payload["messages"] == [{"role": "user", "content": "你是谁？"}]

    payload_text = str(payload["messages"])
    for forbidden in ["FlowScientist", "soft-swimmer", "实验规划", "strict JSON", "tool", "skill"]:
        assert forbidden not in payload_text


def test_debug_payload_rejects_system_history() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/debug_payload",
        json={
            "message": "hello",
            "history": [{"role": "system", "content": "hidden"}],
            "model": "qwen-plus",
        },
    )

    assert response.status_code == 422


def test_main_api_does_not_import_legacy_agent_chain() -> None:
    source = Path("src/main_api.py").read_text(encoding="utf-8")

    for forbidden in ["DialogueOrchestrator", "IntentRouter", "get_default_tools", "get_skill_prompt"]:
        assert forbidden not in source
