from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

import src.main_api as main_api
from src.ai_scientist.agents.base_agent import AgentRun
from src.ai_scientist.exceptions import StructuredOutputError
from src.ai_scientist.job_store import ResearchJobStore
from src.ai_scientist.model_registry import ModelRegistry
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import (
    Claim,
    ClaimEvidenceLink,
    ClaimEvidenceMappingResult,
    ClaimItem,
    DomainSelectionOutput,
    EvidenceItem,
    MethodologyOutput,
    ResearchMode,
    ResearchPhase,
    ResearchQuestion,
)
from src.ai_scientist.structured_client import StructuredCallMetadata, StructuredQwenClient
from src.ai_scientist.schemas import utc_now
from src.ai_scientist.tools.search_tools import QwenEvidenceSearchTool


class TinyOutput(BaseModel):
    value: str


def fake_metadata(agent_name: str, calls: int = 1) -> StructuredCallMetadata:
    now = utc_now()
    return StructuredCallMetadata(
        agent_name=agent_name,
        requested_model=f"{agent_name}-model",
        actual_model=f"{agent_name}-model",
        fallback_used=False,
        started_at=now,
        finished_at=now,
        model_calls=calls,
        attempted_calls=calls,
        successful_calls=calls,
    )


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


def test_structured_client_defers_missing_api_key_until_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    client = StructuredQwenClient(registry=ModelRegistry({"research_director": "test-model"}))

    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY is missing"):
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


def test_structured_stage_failure_keeps_last_complete_phase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert orchestrator.get_project(project.project_id).phase == ResearchPhase.QUESTION_FORMULATION
    events = orchestrator.list_events(project.project_id)
    assert events[-1].status == "failed"
    assert events[-1].error_type == "StructuredOutputError"


def test_research_mode_selection_uses_methodologist_model_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Does remote work improve software engineer productivity?")
    project.phase = ResearchPhase.RESEARCH_MODE_SELECTION
    project.question = ResearchQuestion(
        original_question=project.objective,
        normalized_question=project.objective,
        objective=project.objective,
    )
    orchestrator.store.save(project)
    called: list[str] = []

    def fake_run_agent(project_arg, agent_class):
        called.append(agent_class.__name__)
        return AgentRun(
            output=MethodologyOutput(
                selected_research_mode=ResearchMode.OBSERVATIONAL,
                methodological_rationale="Remote-work productivity is best treated as an observational question.",
            ),
            metadata=fake_metadata("methodologist"),
        )

    monkeypatch.setattr(orchestrator, "_run_agent", fake_run_agent)
    result = orchestrator.run_next_step(project.project_id)
    updated = orchestrator.get_project(project.project_id)

    assert called == ["MethodologistAgent"]
    assert updated.research_mode == ResearchMode.OBSERVATIONAL
    assert result["current_phase"] == ResearchPhase.DOMAIN_SELECTION.value
    assert updated.budget.successful_model_calls == 1


def test_domain_selection_uses_structured_qwen_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Does remote work improve software engineer productivity?")
    project.phase = ResearchPhase.DOMAIN_SELECTION
    project.question = ResearchQuestion(
        original_question=project.objective,
        normalized_question=project.objective,
    )
    project.research_mode = ResearchMode.OBSERVATIONAL
    orchestrator.store.save(project)

    def fake_domain(project_arg):
        return (
            DomainSelectionOutput(
                primary_domain="social_science",
                secondary_domains=["computer_science"],
                confidence=0.9,
                selected_domain_skills=["social_science"],
            ),
            fake_metadata("research_director"),
        )

    monkeypatch.setattr(orchestrator, "_run_domain_selection", fake_domain)
    orchestrator.run_next_step(project.project_id)
    updated = orchestrator.get_project(project.project_id)

    assert updated.domain == "social_science"
    assert "computer_science" in updated.secondary_domains
    assert updated.budget.successful_model_calls == 1


def test_claim_evidence_mapping_uses_evidence_researcher_model_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Map claims")
    project.phase = ResearchPhase.CLAIM_EVIDENCE_MAPPING
    project.evidence = [EvidenceItem(evidence_id="e1", title="Paper", source_type="paper", summary="summary")]
    project.claims = [
        Claim(
            claim_id="c1",
            statement="A supported claim",
            claim_type="reported_fact",
            supporting_evidence_ids=["e1"],
            status="supported",
        )
    ]
    orchestrator.store.save(project)

    def fake_mapping(project_arg, evidence_collection):
        return (
            ClaimEvidenceMappingResult(
                claims=[
                    ClaimItem(
                        claim_id="c1",
                        statement="A supported claim",
                        claim_type="reported_fact",
                        importance="high",
                        status="supported",
                    )
                ],
                links=[
                    ClaimEvidenceLink(
                        claim_id="c1",
                        evidence_id="e1",
                        relation="supports",
                        rationale="The supplied evidence supports the claim.",
                    )
                ],
                evidence_coverage=1.0,
            ),
            fake_metadata("evidence_researcher"),
        )

    monkeypatch.setattr(orchestrator, "_run_claim_mapping", fake_mapping)
    orchestrator.run_next_step(project.project_id)
    updated = orchestrator.get_project(project.project_id)

    assert updated.phase == ResearchPhase.HYPOTHESIS_GENERATION
    assert updated.budget.successful_model_calls == 1


def test_research_api_creates_persistent_safe_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    monkeypatch.setattr(main_api, "research_orchestrator", orchestrator)
    monkeypatch.setattr(main_api, "research_job_store", ResearchJobStore(orchestrator.store.root))
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


def test_research_async_step_returns_persisted_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    monkeypatch.setattr(main_api, "research_orchestrator", orchestrator)
    monkeypatch.setattr(main_api, "research_job_store", ResearchJobStore(orchestrator.store.root))

    def fake_run_next_step(project_id: str, job_id: str | None = None) -> dict:
        project = orchestrator.get_project(project_id)
        previous = project.phase
        orchestrator.state_machine.transition(project, "next")
        orchestrator.store.save(project)
        return {
            "project_id": project_id,
            "previous_phase": previous.value,
            "current_phase": project.phase.value,
            "produced_artifacts": [],
            "human_actions_required": [],
        }

    monkeypatch.setattr(orchestrator, "run_next_step", fake_run_next_step)
    client = TestClient(main_api.app)
    created = client.post(
        "/api/research/start",
        json={"objective": "Async test", "constraints": {}, "max_iterations": 1, "planning_only": True},
    ).json()

    response = client.post(f"/api/research/{created['project_id']}/step_async")
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    for _ in range(20):
        job_response = client.get(f"/api/research/jobs/{job_id}")
        assert job_response.status_code == 200
        job = job_response.json()
        if job["status"] == "completed":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("Async research job did not complete in time.")

    assert job["result"]["previous_phase"] == "INTAKE"
    assert (tmp_path / created["project_id"] / "jobs" / f"{job_id}.json").exists()


def test_research_async_step_rejects_duplicate_active_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    job_store = ResearchJobStore(orchestrator.store.root)
    monkeypatch.setattr(main_api, "research_orchestrator", orchestrator)
    monkeypatch.setattr(main_api, "research_job_store", job_store)
    client = TestClient(main_api.app)
    created = client.post(
        "/api/research/start",
        json={"objective": "Duplicate job test", "constraints": {}, "max_iterations": 1, "planning_only": True},
    ).json()
    active_job = job_store.create(created["project_id"], created["phase"])

    response = client.post(f"/api/research/{created['project_id']}/step_async")

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "project_step_already_running"
    assert response.json()["detail"]["job_id"] == active_job.job_id


def test_existing_qwen_routes_remain_registered() -> None:
    paths = {route.path for route in main_api.app.routes}
    assert {
        "/api/chat", "/api/debug_payload", "/api/qwen_ping",
        "/api/chat_search", "/api/debug_search_payload", "/api/search_ping",
    } <= paths


def test_evidence_search_tool_passes_previous_response_id_as_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class FakeSearchClient:
        def __init__(self, timeout_env=None):
            captured["timeout_env"] = timeout_env

        def search(self, **kwargs):
            captured.update(kwargs)
            return {"reply": "ok", "sources": [], "search_used": True}

    monkeypatch.setattr("src.ai_scientist.tools.search_tools.SearchQwenClient", FakeSearchClient)
    QwenEvidenceSearchTool().run("query", model="evidence-model", previous_response_id="resp_123")

    assert captured["message"] == "query"
    assert captured["model"] == "evidence-model"
    assert captured["previous_response_id"] == "resp_123"
