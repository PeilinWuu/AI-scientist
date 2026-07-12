from pathlib import Path

from fastapi.testclient import TestClient
import pytest

import src.main_api as main_api
from src.ai_scientist.model_registry import ModelRegistry
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.model_utils import normalize_model_name


def test_normalize_model_name_accepts_arbitrary_valid_id() -> None:
    assert normalize_model_name("  qwen3.7-custom-preview  ") == "qwen3.7-custom-preview"
    assert normalize_model_name("") is None
    assert normalize_model_name(None) is None


@pytest.mark.parametrize("value", ["bad\nmodel", "bad\rmodel", "bad\x01model", "x" * 129])
def test_normalize_model_name_rejects_unsafe_text(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_model_name(value)


def test_model_config_endpoint_returns_defaults_without_available_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "qwen-plus")
    monkeypatch.setenv("LLM_SEARCH_MODEL", "qwen3.7-plus")
    client = TestClient(main_api.app)

    response = client.get("/api/config/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pure_qwen"]["default_model"] == "qwen-plus"
    assert payload["qwen_search"]["default_model"] == "qwen3.7-plus"
    assert "available_models" not in payload["pure_qwen"]
    assert "DASHSCOPE_API_KEY" not in str(payload)


def test_model_overrides_take_priority_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SCIENTIST_DIRECTOR_MODEL", "env-director")
    monkeypatch.delenv("AI_SCIENTIST_ANALYST_MODEL", raising=False)
    monkeypatch.setenv("AI_SCIENTIST_FALLBACK_MODEL", "env-fallback")

    registry = ModelRegistry({"research_director": "project-director", "fallback": "project-fallback"})
    director = registry.resolve("research_director")
    analyst = registry.resolve("analyst")

    assert director.actual_model == "project-director"
    assert director.fallback_used is False
    assert analyst.actual_model == "project-fallback"
    assert analyst.fallback_used is True


def test_created_project_persists_model_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    monkeypatch.setenv("AI_SCIENTIST_DIRECTOR_MODEL", "env-director")
    project = orchestrator.create_project(
        "Model override persistence",
        model_overrides={"research_director": "project-director"},
    )
    monkeypatch.setenv("AI_SCIENTIST_DIRECTOR_MODEL", "changed-env-director")

    loaded = orchestrator.get_project(project.project_id)
    resolved = ModelRegistry(loaded.model_overrides).resolve("research_director")

    assert loaded.model_overrides == {"research_director": "project-director"}
    assert resolved.actual_model == "project-director"


def test_model_test_endpoint_classifies_quota_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePureClient:
        def chat(self, *args, **kwargs):
            raise RuntimeError("quota exhausted")

    monkeypatch.setattr(main_api, "PureQwenClient", lambda: FakePureClient())
    client = TestClient(main_api.app)

    response = client.post("/api/models/test", json={"model": "qwen-test", "mode": "chat"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["error_category"] == "quota_exhausted"
    assert "key" not in str(payload).lower()


def test_streamlit_uses_free_text_model_inputs() -> None:
    source = Path("app_streamlit.py").read_text(encoding="utf-8")

    assert "pure_qwen_model_input" in source
    assert "qwen_search_model_input" in source
    assert "PURE_MODEL_OPTIONS" not in source
    assert "SEARCH_MODEL_OPTIONS" not in source
    assert 'st.sidebar.selectbox(\n            "Model"' not in source
