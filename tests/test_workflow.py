"""Tests for end-to-end workflow artifact persistence."""

from __future__ import annotations

from pathlib import Path

from src.config import settings
from src.schemas import Constraints, RunRequest
from src.workflow.experiment_loop import run_experiment_loop


def test_workflow_saves_two_iterations_and_reports(tmp_path: Path) -> None:
    settings.llm_provider = "mock"
    settings.qwen_require_real = False
    request = RunRequest(
        research_goal="Improve soft swimmer efficiency with stable motion.",
        constraints=Constraints(
            min_stability=0.65,
            max_energy_cost=2.2,
            target_metric="efficiency",
        ),
        max_iterations=2,
        random_seed=11,
    )

    summary = run_experiment_loop(request, runs_dir=tmp_path)
    run_dir = tmp_path / summary.run_id

    assert (run_dir / "iteration_1_plan.json").exists()
    assert (run_dir / "iteration_1_results.csv").exists()
    assert (run_dir / "iteration_1_analysis.json").exists()
    assert (run_dir / "iteration_1_feedback.json").exists()
    assert (run_dir / "iteration_2_plan.json").exists()
    assert (run_dir / "iteration_2_results.csv").exists()
    assert (run_dir / "iteration_2_analysis.json").exists()
    assert (run_dir / "iteration_2_feedback.json").exists()
    assert (run_dir / "final_report.md").exists()
    assert (run_dir / "final_report.json").exists()
