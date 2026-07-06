"""End-to-end FlowScientist-Loop experiment workflow."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from src.agents.critic import CriticAgent
from src.agents.data_analyst import DataAnalystAgent
from src.agents.experiment_planner import ExperimentPlannerAgent
from src.agents.problem_analyst import ProblemAnalystAgent
from src.agents.report_writer import ReportWriterAgent
from src.config import settings
from src.llm import get_llm_provider
from src.schemas import RunRequest, RunSummary
from src.simulator.soft_swimmer_simulator import SoftSwimmerSimulator
from src.utils.llm_audit import LLMCallRecorder
from src.utils.io import ensure_dir, read_json, write_json


def run_experiment_loop(request: RunRequest, runs_dir: Path | None = None) -> RunSummary:
    """Run the full closed-loop virtual experiment and save all artifacts."""

    root = runs_dir or settings.runs_dir
    ensure_dir(root)
    run_id = _new_run_id()
    run_dir = root / run_id
    ensure_dir(run_dir)

    llm = get_llm_provider()
    recorder = LLMCallRecorder(run_dir, llm)
    llm_metadata = _normalize_llm_metadata(llm.metadata())
    config_data = {**request.model_dump(), **llm_metadata}
    write_json(run_dir / "config.json", config_data)
    write_json(run_dir / "metadata.json", llm_metadata)
    print(f"Using LLM provider: {llm_metadata['llm_provider']}")
    print(f"Using LLM transport: {llm_metadata['llm_transport']}")
    print(f"Using model: {llm_metadata['llm_model']}")
    print(f"Mock mode: {str(llm_metadata['is_mock']).lower()}")

    problem_agent = ProblemAnalystAgent(llm)
    planner = ExperimentPlannerAgent()
    simulator = SoftSwimmerSimulator(request.constraints, random_seed=request.random_seed)
    analyst = DataAnalystAgent()
    critic = CriticAgent()
    reporter = ReportWriterAgent()

    problem = problem_agent.analyze(
        request.research_goal, request.constraints, request.human_feedback, recorder
    )
    llm_metadata = _normalize_llm_metadata(llm.metadata())
    config_data.update(llm_metadata)
    write_json(run_dir / "config.json", config_data)
    write_json(run_dir / "metadata.json", llm_metadata)
    write_json(run_dir / "problem_analysis.json", problem)

    history: list[dict[str, Any]] = []
    previous_best: dict[str, Any] | None = None

    for iteration in range(1, request.max_iterations + 1):
        plan = planner.plan(
            iteration, request.constraints, problem, history, llm=llm, recorder=recorder
        )
        write_json(run_dir / f"iteration_{iteration}_plan.json", plan.model_dump())

        candidate_pairs = [
            (candidate.candidate_id, candidate.params) for candidate in plan.candidates
        ]
        results = simulator.run_batch(candidate_pairs, iteration)
        results_data = [result.model_dump() for result in results]
        pd.DataFrame(results_data).to_csv(
            run_dir / f"iteration_{iteration}_results.csv", index=False
        )

        analysis = analyst.analyze(
            iteration,
            results_data,
            request.constraints,
            previous_best,
            problem=problem,
            llm=llm,
            recorder=recorder,
        )
        write_json(run_dir / f"iteration_{iteration}_analysis.json", analysis)

        feedback = critic.critique(
            analysis,
            request.constraints,
            previous_best,
            research_goal=request.research_goal,
            problem=problem,
            iteration_results=results_data,
            history=history,
            llm=llm,
            recorder=recorder,
        )
        write_json(run_dir / f"iteration_{iteration}_feedback.json", feedback)

        history_item = {
            "iteration": iteration,
            "plan": plan.model_dump(),
            "results": results_data,
            "analysis": analysis,
            "feedback": feedback,
        }
        history.append(history_item)
        previous_best = _select_global_best(
            previous_best, analysis["best_candidate"], request.constraints.target_metric
        )

    report_md, report_json = reporter.write(
        run_id,
        request.research_goal,
        request.constraints,
        problem,
        history,
        request.human_feedback,
        llm_metadata,
        llm=llm,
        recorder=recorder,
    )
    llm_metadata = {
        **llm_metadata,
        "total_llm_calls": recorder.count(),
        "llm_calls_path": str(recorder.calls_dir),
        "last_llm_response_excerpt": recorder.last_response_excerpt(),
    }
    if settings.qwen_require_real and recorder.count() == 0:
        raise RuntimeError("Real Qwen is required but no LLM calls were recorded.")
    config_data.update(llm_metadata)
    write_json(run_dir / "config.json", config_data)
    write_json(run_dir / "metadata.json", llm_metadata)
    (run_dir / "final_report.md").write_text(report_md, encoding="utf-8")
    write_json(run_dir / "final_report.json", report_json)

    best_iteration = report_json.get("best_iteration")
    best_candidate = report_json.get("best_candidate")
    return RunSummary(
        run_id=run_id,
        best_candidate=best_candidate,
        best_iteration=best_iteration,
        final_report_path=str(run_dir / "final_report.md"),
        message=f"Completed {request.max_iterations} feedback iterations.",
    )


def get_run(run_id: str, runs_dir: Path | None = None) -> dict[str, Any]:
    """Load one run with config, iteration artifacts, and report if present."""

    run_dir = _run_dir(run_id, runs_dir)
    config = read_json(run_dir / "config.json")
    history = load_history(run_id, runs_dir)
    report_path = run_dir / "final_report.md"
    report_json_path = run_dir / "final_report.json"
    return {
        "run_id": run_id,
        "config": config,
        "history": history,
        "final_report": report_path.read_text(encoding="utf-8")
        if report_path.exists()
        else None,
        "final_report_json": read_json(report_json_path) if report_json_path.exists() else None,
        "run_dir": str(run_dir),
    }


def load_history(run_id: str, runs_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load all iteration logs for a run."""

    run_dir = _run_dir(run_id, runs_dir)
    history: list[dict[str, Any]] = []
    iteration = 1
    while (run_dir / f"iteration_{iteration}_plan.json").exists():
        results_path = run_dir / f"iteration_{iteration}_results.csv"
        history.append(
            {
                "iteration": iteration,
                "plan": read_json(run_dir / f"iteration_{iteration}_plan.json"),
                "results": pd.read_csv(results_path).to_dict(orient="records"),
                "analysis": read_json(run_dir / f"iteration_{iteration}_analysis.json"),
                "feedback": read_json(run_dir / f"iteration_{iteration}_feedback.json"),
            }
        )
        iteration += 1
    return history


def get_report(run_id: str, runs_dir: Path | None = None) -> str:
    """Read the final Markdown report."""

    run_dir = _run_dir(run_id, runs_dir)
    return (run_dir / "final_report.md").read_text(encoding="utf-8")


def add_human_feedback(
    run_id: str, human_feedback: str, runs_dir: Path | None = None
) -> dict[str, Any]:
    """Append human feedback and regenerate next-plan preview plus report."""

    run_dir = _run_dir(run_id, runs_dir)
    config_data = read_json(run_dir / "config.json")
    old_feedback = config_data.get("human_feedback")
    config_data["human_feedback"] = (
        f"{old_feedback}\n{human_feedback}" if old_feedback else human_feedback
    )
    write_json(run_dir / "config.json", config_data)

    request = RunRequest(**config_data)
    llm = get_llm_provider()
    recorder = LLMCallRecorder(run_dir, llm)
    problem = ProblemAnalystAgent(llm).analyze(
        request.research_goal, request.constraints, request.human_feedback, recorder
    )
    llm_metadata = _normalize_llm_metadata(llm.metadata())
    config_data.update(llm_metadata)
    write_json(run_dir / "config.json", config_data)
    write_json(run_dir / "metadata.json", llm_metadata)
    history = load_history(run_id, runs_dir)

    next_iteration = len(history) + 1
    next_plan = ExperimentPlannerAgent().plan(
        next_iteration, request.constraints, problem, history
    )
    write_json(run_dir / "post_feedback_next_plan.json", next_plan.model_dump())

    report_md, report_json = ReportWriterAgent().write(
        run_id,
        request.research_goal,
        request.constraints,
        problem,
        history,
        request.human_feedback,
        llm_metadata,
        llm=llm,
        recorder=recorder,
    )
    (run_dir / "final_report.md").write_text(report_md, encoding="utf-8")
    write_json(run_dir / "final_report.json", report_json)

    return {
        "run_id": run_id,
        "human_feedback": request.human_feedback,
        "next_plan_preview": next_plan.model_dump(),
        "message": "Human feedback appended; next-plan preview and report were regenerated.",
    }


def _new_run_id() -> str:
    """Create a readable unique run id."""

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{uuid4().hex[:8]}"


def _run_dir(run_id: str, runs_dir: Path | None = None) -> Path:
    """Resolve and validate a run directory path."""

    root = runs_dir or settings.runs_dir
    run_dir = root / run_id
    if not run_dir.exists():
        raise FileNotFoundError(f"Run not found: {run_id}")
    return run_dir


def _select_global_best(
    previous_best: dict[str, Any] | None, candidate: dict[str, Any], target_metric: str
) -> dict[str, Any]:
    """Keep the best candidate so far according to the selected metric."""

    if previous_best is None:
        return candidate
    current = candidate[target_metric]
    previous = previous_best[target_metric]
    is_better = current < previous if target_metric == "energy_cost" else current > previous
    return candidate if is_better else previous_best


def _normalize_llm_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Map provider metadata to run-level audit fields."""

    return {
        "llm_provider": metadata.get("provider", "unknown"),
        "llm_transport": metadata.get("transport", "unknown"),
        "llm_model": metadata.get("model", settings.llm_model),
        "llm_base_url": metadata.get("base_url", settings.llm_base_url),
        "is_mock": bool(metadata.get("is_mock", True)),
    }
