from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

import src.main_api as main_api
from src.ai_scientist.exceptions import StructuredOutputError
from src.ai_scientist.model_registry import ModelRegistry
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import ResearchPhase
from src.ai_scientist.structured_client import StructuredQwenClient


class TinyOutput(BaseModel):
    value: str


def test_model_registry_records_configuration_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_SCIENTIST_DIRECTOR_MODEL", raising=False)
    monkeypatch.setenv("AI_SCIENTIST_FALLBACK_MODEL", "fallback-model")
    resolution = ModelRegistry().resolve("research_director")
    assert resolution.actual_model == "fallback-model"
    assert resolution.fallback_used is True


def test_structured_client_repairs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("AI_SCIENTIST_DIRECTOR_MODEL", "director-model")
    client = StructuredQwenClient()
    responses = iter([
        ('{"wrong":"shape"}', {}, "director-model", False),
        ('{"value":"repaired"}', {"total_tokens": 4}, "director-model", False),
    ])
    monkeypatch.setattr(client, "_request_with_fallback", lambda *args, **kwargs: next(responses))
    result = client.call("research_director", "instructions", {"input": "x"}, TinyOutput)
    assert result.value.value == "repaired"
    assert result.metadata.model_calls == 2


def test_structured_client_fails_after_one_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    client = StructuredQwenClient()
    responses = iter([
        ('{"wrong":1}', {}, "model", False),
        ('{"still_wrong":2}', {}, "model", False),
    ])
    monkeypatch.setattr(client, "_request_with_fallback", lambda *args, **kwargs: next(responses))
    with pytest.raises(StructuredOutputError):
        client.call("research_director", "instructions", {}, TinyOutput)


def test_structured_client_records_runtime_model_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("AI_SCIENTIST_DIRECTOR_MODEL", "unavailable-model")
    monkeypatch.setenv("AI_SCIENTIST_FALLBACK_MODEL", "fallback-model")
    client = StructuredQwenClient()

    def fake_request(model: str, messages: list[dict[str, str]]) -> tuple[str, dict[str, int]]:
        if model == "unavailable-model":
            raise RuntimeError("model unavailable")
        return '{"value":"fallback result"}', {"total_tokens": 3}

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.call("research_director", "instructions", {}, TinyOutput)
    assert result.metadata.requested_model == "unavailable-model"
    assert result.metadata.actual_model == "fallback-model"
    assert result.metadata.fallback_used is True
    assert result.metadata.model_calls == 2


def test_structured_stage_failure_marks_project_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Test structured failure")
    project.phase = ResearchPhase.QUESTION_FORMULATION
    orchestrator.store.save(project)
    monkeypatch.setattr(
        orchestrator,
        "_run_agent",
        lambda *args, **kwargs: (_ for _ in ()).throw(StructuredOutputError("invalid twice")),
    )
    with pytest.raises(StructuredOutputError):
        orchestrator.run_next_step(project.project_id)
    assert orchestrator.get_project(project.project_id).phase.value == "FAILED"


def test_research_api_creates_persistent_safe_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_api, "research_orchestrator", ResearchOrchestrator(tmp_path))
    client = TestClient(main_api.app)
    response = client.post(
        "/api/research/start",
        json={
            "objective": "Study a general scientific question",
            "domain_hint": None,
            "constraints": {},
            "max_iterations": 2,
            "planning_only": True,
        },
    )
    assert response.status_code == 200
    created = response.json()
    assert created["phase"] == "INTAKE"
    project_response = client.get(f"/api/research/{created['project_id']}")
    assert project_response.status_code == 200
    project = project_response.json()
    assert set(project).isdisjoint({"system_prompt", "raw_response", "authorization", "api_key"})
    assert (tmp_path / created["project_id"] / "project.json").exists()


def test_existing_qwen_routes_remain_registered() -> None:
    paths = {route.path for route in main_api.app.routes}
    assert {
        "/api/chat", "/api/debug_payload", "/api/qwen_ping",
        "/api/chat_search", "/api/debug_search_payload", "/api/search_ping",
    } <= paths
