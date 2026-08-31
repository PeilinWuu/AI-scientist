from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from src.ai_scientist.competition_runtime import CompetitionRuntime
from src.ai_scientist.competition_schemas import ExecutionRequest
from src.ai_scientist.tools.execution_adapter import ExecutionAdapter
from src.main_api import app


def test_executor_validates_and_inspects_project_dataset(tmp_path: Path) -> None:
    pd.DataFrame({"x": [1, 2, 3], "y": [2, 4, 6]}).to_csv(tmp_path / "data.csv", index=False)
    adapter = ExecutionAdapter(tmp_path)
    result = adapter.execute(ExecutionRequest(
        operation="inspect_dataset",
        inputs={"dataset_path": "data.csv"},
        provenance={"asset_id": "asset_test"},
        output_directory="results/inspect",
    ))
    assert result["status"] == "success"
    assert result["metrics"]["rows"] == 3
    assert result["input_checksums"]["dataset_path"]
    assert result["input_fingerprint"]
    assert result["software_versions"]["python"]
    assert (tmp_path / result["artifacts"][0]["relative_path"]).exists()


def test_executor_runs_regression_and_two_plot_types(tmp_path: Path) -> None:
    pd.DataFrame({"x": [1, 2, 3, 4, 5], "y": [3, 5, 7, 9, 11]}).to_csv(tmp_path / "data.csv", index=False)
    adapter = ExecutionAdapter(tmp_path)
    regression = adapter.execute({
        "operation": "linear_regression",
        "inputs": {"dataset_path": "data.csv"},
        "parameters": {"target": "y", "features": ["x"]},
        "output_directory": "results/regression",
    })
    histogram = adapter.execute({
        "operation": "plot_histogram",
        "inputs": {"dataset_path": "data.csv"},
        "parameters": {"column": "x", "bins": 3},
        "output_directory": "results/histogram",
    })
    scatter = adapter.execute({
        "operation": "plot_scatter",
        "inputs": {"dataset_path": "data.csv"},
        "parameters": {"x": "x", "y": "y"},
        "output_directory": "results/scatter",
    })
    assert regression["status"] == "success"
    assert regression["metrics"]["r_squared"] == 1.0
    assert histogram["artifacts"][0]["media_type"] == "image/png"
    assert scatter["artifacts"][0]["media_type"] == "image/png"


def test_executor_rejects_unknown_operation_missing_file_and_path_escape(tmp_path: Path) -> None:
    adapter = ExecutionAdapter(tmp_path)
    unknown = adapter.execute({"operation": "python_exec", "parameters": {"code": "2 + 2"}})
    missing = adapter.execute({"operation": "inspect_dataset", "inputs": {"dataset_path": "missing.csv"}})
    escape = adapter.execute({"operation": "inspect_dataset", "inputs": {"dataset_path": "../outside.csv"}})
    assert unknown["status"] == "rejected"
    assert missing["status"] == "rejected"
    assert "does not exist" in missing["failure_reason"]
    assert escape["status"] == "rejected"
    assert "project boundary" in escape["failure_reason"]


def test_flagship_feedback_changes_round_two_and_beats_baseline(tmp_path: Path) -> None:
    state = CompetitionRuntime(tmp_path).run_flagship(seed=20260831)
    assert state.status == "complete"
    assert len(state.iterations) == 2
    assert state.plans[1].derived_from_execution_id == state.iterations[0].execution_ids[0]
    adjustment = state.iterations[0].adjustments[0]
    assert adjustment.old_value != adjustment.new_value
    assert adjustment.evidence_refs
    assert state.comparison["iteration"]["round_2_rmse"] < state.comparison["iteration"]["round_1_rmse"]
    assert state.comparison["baseline"]["iterative_final_rmse"] < state.comparison["baseline"]["one_shot_baseline_rmse"]
    assert (tmp_path / "round_1/plan.json").exists()
    assert (tmp_path / "feedback/plan_adjustments.json").exists()
    assert (tmp_path / "round_2/analysis/evaluation.json").exists()
    assert (tmp_path / "audit/event_log_excerpt.jsonl").exists()


def test_failure_cases_are_structured_and_actionable(tmp_path: Path) -> None:
    runtime = CompetitionRuntime(tmp_path / "competition/1b/cases/flagship")
    failures = runtime.run_failure_cases()
    assert len(failures) >= 3
    assert all(item["detected"] for item in failures)
    assert all(item["error"] for item in failures)
    assert {item["next_action"] for item in failures} >= {"correct_input", "human_review"}


def test_competition_api_happy_path_and_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_SCIENTIST_COMPETITION_DIR", str(tmp_path))
    client = TestClient(app)
    readiness = client.get("/api/competition/1b/readiness")
    assert readiness.status_code == 200
    response = client.post("/api/competition/1b/demo/run", json={"seed": 20260831})
    assert response.status_code == 200
    assert response.json()["status"] == "complete"
    assert client.get("/api/competition/1b/demo").status_code == 200
    history = client.get("/api/competition/1b/demo/history")
    assert history.status_code == 200
    assert any(item["event_type"] == "feedback_generated" for item in history.json())
    artifacts = client.get("/api/competition/1b/demo/artifacts")
    assert artifacts.status_code == 200
    assert any(item["relative_path"] == "feedback/plan_adjustments.json" for item in artifacts.json())


def test_competition_api_rejects_invalid_request_and_missing_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_SCIENTIST_COMPETITION_DIR", str(tmp_path))
    client = TestClient(app)
    assert client.get("/api/competition/1b/demo").status_code == 404
    invalid = client.post("/api/competition/1b/demo/run", json={"seed": -1})
    assert invalid.status_code == 422
