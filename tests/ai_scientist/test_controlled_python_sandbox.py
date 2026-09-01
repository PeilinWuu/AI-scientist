from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import main_api
from src.ai_scientist.job_store import ResearchJobStore
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import ResearchMode, ResearchPhase
from src.ai_scientist.tools.controlled_python_sandbox import ControlledPythonSandbox
from src.ai_scientist.tools.controlled_python_worker import validate_code
from src.ai_scientist.tools.registry import ToolRegistry


@pytest.mark.parametrize(
    "code, expected",
    [
        ("import os\nresult = 1", "Import"),
        ("result = open('x.txt')", "open"),
        ("result = data.__class__", "__class__"),
        ("result = pd.read_csv('x.csv')", "read_csv"),
        ("result = np.fromfile('x.bin')", "fromfile"),
        ("result = data.to_pickle('x.pkl')", "to_pickle"),
        ("result = data.query('x > 1')", "query"),
        ("result = np.lib.format.open_memmap('x.bin')", "open_memmap"),
        ("result = 'https://example.com'", "URL"),
        ("result = '/outside.csv'", "absolute"),
        ("result = '../outside.csv'", "parent-relative"),
    ],
)
def test_validator_rejects_escape_surfaces(code: str, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        validate_code(code)


def test_controlled_python_executes_in_memory_analysis_and_redacts_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    dataset = project_root / "data.csv"
    dataset.write_text("group,x,y\na,1,2\na,2,4\nb,3,5\nb,4,8\n", encoding="utf-8")
    secret = "sandbox-secret-value-123"
    monkeypatch.setenv("TEST_SECRET_TOKEN", secret)
    code = (
        "correlation = float(data['x'].corr(data['y']))\n"
        f"result = {{'rows': len(data), 'correlation': correlation, 'note': '{secret}'}}"
    )

    audit = ControlledPythonSandbox(project_root).execute(
        code=code,
        dataset_path=dataset,
        timeout_seconds=10,
        memory_limit_mb=512,
        seed=7,
    )

    serialized = json.dumps(audit, ensure_ascii=False)
    assert audit["status"] == "success"
    assert audit["result"]["rows"] == 4
    assert audit["result"]["correlation"] > 0.98
    assert secret not in serialized
    assert audit["isolation"]["network_allowed"] is False
    assert audit["isolation"]["parent_environment_inherited"] is False
    assert audit["artifacts"]
    artifact = project_root / audit["artifacts"][0]["relative_path"]
    assert secret not in artifact.read_text(encoding="utf-8")
    run_workspace = artifact.parent
    assert not (run_workspace / "request.json").exists()
    assert not (run_workspace / "worker_result.json").exists()


def test_controlled_python_enforces_timeout(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    dataset = project_root / "data.csv"
    dataset.write_text("x\n1\n", encoding="utf-8")

    audit = ControlledPythonSandbox(project_root).execute(
        code="while True:\n    pass\nresult = 1",
        dataset_path=dataset,
        timeout_seconds=1,
        memory_limit_mb=512,
    )

    assert audit["status"] == "timeout"
    assert audit["error_type"] == "SandboxLimitError"


def test_orchestrator_records_controlled_python_and_returns_to_review(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Compute a custom statistic", planning_only=False)
    project.research_mode = ResearchMode.DATA_ANALYSIS
    orchestrator.store.save(project)
    project = orchestrator.register_research_asset(
        project.project_id,
        "data.csv",
        "text/csv",
        b"x,y\n1,2\n2,4\n3,6\n",
        purpose="data",
    )
    project.phase = ResearchPhase.COMPLETED
    orchestrator.store.save(project)

    updated, audit = orchestrator.run_controlled_python(
        project.project_id,
        code="result = {'slope': float(np.polyfit(data['x'], data['y'], 1)[0])}",
        timeout_seconds=10,
        memory_limit_mb=512,
    )

    assert audit["status"] == "success"
    assert audit["result"]["slope"] == pytest.approx(2.0)
    assert updated.phase == ResearchPhase.CRITICAL_REVIEW
    assert updated.conclusion is None
    assert updated.controlled_python_runs[-1]["audit_artifact_id"]
    assert updated.controlled_python_runs[-1]["execution_evidence_id"]
    assert updated.controlled_python_runs[-1]["generated_claim_id"]
    assert any(item.artifact_type == "controlled_python_run" for item in updated.artifacts)


def test_api_feature_flag_and_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    monkeypatch.setattr(main_api, "research_orchestrator", orchestrator)
    monkeypatch.setattr(main_api, "research_job_store", ResearchJobStore(orchestrator.store.root))
    client = TestClient(main_api.app)
    project = orchestrator.create_project("Analyze data", planning_only=False)

    monkeypatch.delenv("AI_SCIENTIST_ENABLE_CONTROLLED_PYTHON", raising=False)
    response = client.post(
        f"/api/research/{project.project_id}/controlled-python",
        json={"code": "result = 1"},
    )
    assert response.status_code == 403
    assert ToolRegistry().get("python_executor").available is False

    monkeypatch.setenv("AI_SCIENTIST_ENABLE_CONTROLLED_PYTHON", "1")
    assert client.get("/health").json()["controlled_python_sandbox_enabled"] is True
    assert ToolRegistry().get("python_executor").available is True
