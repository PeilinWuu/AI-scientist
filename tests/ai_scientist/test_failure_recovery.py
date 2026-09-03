from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import BadRequestError, InternalServerError, OpenAI

from src import main_api
from src.ai_scientist.job_store import ResearchJobStore, fail_job
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.presentation import (
    model_service_error_message,
    render_project_overview,
    research_step_action_state,
    safe_error_debug_details,
)
from src.ai_scientist.structured_client import StructuredQwenClient
from src.model_utils import model_max_retries
from src.pure_qwen_client import PureQwenClient
from src.search_qwen_client import SearchQwenClient


def test_failed_job_is_inactive_and_same_project_can_retry_without_losing_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    jobs = ResearchJobStore(orchestrator.store.root)
    monkeypatch.setattr(main_api, "research_orchestrator", orchestrator)
    monkeypatch.setattr(main_api, "research_job_store", jobs)
    project = orchestrator.create_project("Recover the same project")
    failed = jobs.create(project.project_id, project.phase.value)
    jobs.save(fail_job(failed, TimeoutError("provider timeout")))
    assert jobs.active_step_job(project.project_id) is None
    before_project = orchestrator.get_project(project.project_id).model_dump(mode="json")
    before_events = [item.model_dump(mode="json") for item in orchestrator.list_events(project.project_id)]

    class DeferredThread:
        def __init__(self, *, target, args, name, daemon):
            self.target, self.args = target, args

        def start(self) -> None:
            return None

    monkeypatch.setattr(main_api.threading, "Thread", DeferredThread)
    response = TestClient(main_api.app).post(f"/api/research/{project.project_id}/step_async")

    assert jobs.active_step_job(project.project_id) is not None
    assert response.status_code == 202
    assert response.json()["project_id"] == project.project_id
    assert response.json()["job_id"] != failed.job_id
    assert orchestrator.get_project(project.project_id).model_dump(mode="json") == before_project
    assert [item.model_dump(mode="json") for item in orchestrator.list_events(project.project_id)] == before_events


def test_running_job_still_blocks_a_duplicate_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    jobs = ResearchJobStore(orchestrator.store.root)
    monkeypatch.setattr(main_api, "research_orchestrator", orchestrator)
    monkeypatch.setattr(main_api, "research_job_store", jobs)
    project = orchestrator.create_project("Do not duplicate running work")
    active = jobs.create(project.project_id, project.phase.value)
    active.status = "running"
    jobs.save(active)

    response = TestClient(main_api.app).post(f"/api/research/{project.project_id}/step_async")

    assert response.status_code == 409
    assert response.json()["detail"]["job_id"] == active.job_id


@pytest.mark.parametrize("status", ["queued", "running"])
def test_restart_recovers_orphaned_active_job_as_auditable_failure(tmp_path: Path, status: str) -> None:
    jobs = ResearchJobStore(tmp_path)
    job = jobs.create("project_test", "BACKGROUND_RESEARCH")
    job.status = status
    jobs.save(job)

    recovered = ResearchJobStore(tmp_path).recover_orphaned_jobs()

    saved = jobs.load(job.project_id, job.job_id)
    assert [item.job_id for item in recovered] == [job.job_id]
    assert saved.status == "failed"
    assert saved.error == {
        "error_type": "worker_restarted",
        "error_message": (
            "The API worker restarted before this stage job completed; "
            "the project remains at its last persisted phase and can be retried."
        ),
        "failure_category": "interrupted",
        "stage": "BACKGROUND_RESEARCH",
    }
    assert saved.finished_at is not None
    assert jobs.active_step_job(job.project_id) is None


def test_failure_and_cancel_have_distinct_ui_actions_and_labels() -> None:
    assert research_step_action_state("BACKGROUND_RESEARCH", "failed") == {
        "label": "🔄 重试本阶段", "disabled": False,
    }
    assert research_step_action_state("BACKGROUND_RESEARCH", "running") == {
        "label": "⏳ 当前阶段正在运行", "disabled": True,
    }
    cancelled = research_step_action_state("CANCELLED", None)
    assert cancelled["disabled"] is True
    assert cancelled["label"] != "🔄 重试本阶段"
    assert "项目已取消" in render_project_overview({"phase": "CANCELLED", "budget": {}})
    assert research_step_action_state("COMPLETED", None) == {
        "label": "✅ 研究流程已完成", "disabled": True,
    }
    assert research_step_action_state("FAILED", "failed") == {
        "label": "研究项目已失败", "disabled": True,
    }


def test_provider_errors_have_safe_recovery_messages_and_redacted_debug_details() -> None:
    assert "响应超时" in model_service_error_message({"error_type": "APITimeoutError"})
    assert "连接暂时失败" in model_service_error_message({"error_type": "APIConnectionError"})
    assert "速率限制" in model_service_error_message({"error_type": "RateLimitError", "status_code": 429})
    safe = safe_error_debug_details({
        "error_type": "APITimeoutError",
        "error_message": "Authorization: Bearer secret-token at /opt/ai-scientist/private/file.py",
    })
    serialized = json.dumps(safe)
    assert "secret-token" not in serialized
    assert "/opt/ai-scientist" not in serialized


def test_model_retry_setting_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SCIENTIST_MODEL_MAX_RETRIES", "1")
    assert model_max_retries() == 1
    monkeypatch.setenv("AI_SCIENTIST_MODEL_MAX_RETRIES", "999")
    assert model_max_retries() == 2
    monkeypatch.setenv("AI_SCIENTIST_MODEL_MAX_RETRIES", "invalid")
    assert model_max_retries() == 1


def test_all_model_clients_read_retry_and_scoped_timeout_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("AI_SCIENTIST_MODEL_MAX_RETRIES", "1")
    monkeypatch.setenv("LLM_TIMEOUT", "111")
    monkeypatch.setenv("AI_SCIENTIST_MODEL_TIMEOUT", "222")
    monkeypatch.setenv("AI_SCIENTIST_SEARCH_QUERY_TIMEOUT", "333")
    pure = PureQwenClient()
    structured = StructuredQwenClient()
    search = SearchQwenClient(timeout_env="AI_SCIENTIST_SEARCH_QUERY_TIMEOUT")
    structured_client = structured._get_client()

    assert pure.timeout == 111
    assert structured.http_client.timeout.read == 222
    assert search.timeout == 333
    assert pure.client.max_retries == structured_client.max_retries == search.client.max_retries == 1
    pure.http_client.close()
    structured.http_client.close()
    search.http_client.close()


@pytest.mark.parametrize(
    "status_code, exception_type, expected_requests",
    [(400, BadRequestError, 1), (500, InternalServerError, 2)],
)
def test_sdk_retries_only_bounded_recoverable_responses(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    exception_type: type[Exception],
    expected_requests: int,
) -> None:
    requests_seen = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests_seen
        requests_seen += 1
        return httpx.Response(
            status_code,
            request=request,
            headers={"x-request-id": "request_test"},
            json={"error": {"message": "safe test error", "type": "test_error"}},
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("AI_SCIENTIST_MODEL_MAX_RETRIES", "1")
    client = PureQwenClient()
    client.http_client.close()
    client.http_client = httpx.Client(transport=transport, timeout=client.timeout)
    client.client = OpenAI(
        api_key="test-key",
        base_url="https://provider.test/v1",
        http_client=client.http_client,
        max_retries=model_max_retries(),
    )

    with pytest.raises(exception_type):
        client.chat([{"role": "user", "content": "test"}])
    assert requests_seen == expected_requests
