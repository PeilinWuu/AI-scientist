"""Run two one-iteration real-Qwen cases and compare planning sensitivity."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import settings
from src.schemas import Constraints, RunRequest
from src.workflow.experiment_loop import run_experiment_loop


def main() -> int:
    """Run two goals and fail if the plans are nearly identical."""

    if settings.llm_provider != "qwen" or settings.qwen_transport != "curl":
        print("This sensitivity test requires LLM_PROVIDER=qwen and QWEN_TRANSPORT=curl.")
        return 1
    if settings.qwen_require_real is not True:
        print("This sensitivity test requires QWEN_REQUIRE_REAL=true.")
        return 1

    cases = [
        RunRequest(
            research_goal="Maximize swimming speed even if energy cost increases moderately.",
            constraints=Constraints(
                min_stability=0.6,
                max_energy_cost=3.0,
                target_metric="mean_speed",
            ),
            max_iterations=1,
            random_seed=101,
        ),
        RunRequest(
            research_goal="Minimize energy cost and prioritize stable swimming motion.",
            constraints=Constraints(
                min_stability=0.75,
                max_energy_cost=1.4,
                target_metric="energy_cost",
            ),
            max_iterations=1,
            random_seed=102,
        ),
    ]

    summaries = []
    for request in cases:
        summary = run_experiment_loop(request)
        summaries.append(_summarize_run(summary.run_id))

    df = pd.DataFrame(summaries)
    print(df.to_string(index=False))

    different_problem = (
        df.loc[0, "target_metric"] != df.loc[1, "target_metric"]
        or df.loc[0, "planning_preference"] != df.loc[1, "planning_preference"]
    )
    plan_delta = max(
        abs(float(df.loc[0, "avg_frequency"]) - float(df.loc[1, "avg_frequency"])),
        abs(float(df.loc[0, "avg_amplitude"]) - float(df.loc[1, "avg_amplitude"])),
        abs(float(df.loc[0, "avg_stiffness"]) - float(df.loc[1, "avg_stiffness"])),
    )
    if not different_problem or plan_delta < 0.05:
        print("Goal sensitivity test failed: goals produced nearly identical planning.")
        return 1
    print("Goal sensitivity test passed.")
    return 0


def _summarize_run(run_id: str) -> dict:
    run_dir = settings.runs_dir / run_id
    problem = json.loads((run_dir / "problem_analysis.json").read_text(encoding="utf-8"))
    plan = json.loads((run_dir / "iteration_1_plan.json").read_text(encoding="utf-8"))
    params = [candidate["params"] for candidate in plan["candidates"]]
    llm_calls = len(list((run_dir / "llm_calls").glob("*_response.json")))
    return {
        "run_id": run_id,
        "target_metric": problem["target_metric"],
        "planning_preference": problem["planning_preference"],
        "avg_frequency": round(sum(item["frequency"] for item in params) / len(params), 4),
        "avg_amplitude": round(sum(item["amplitude"] for item in params) / len(params), 4),
        "avg_stiffness": round(sum(item["stiffness"] for item in params) / len(params), 4),
        "llm_calls_count": llm_calls,
    }


if __name__ == "__main__":
    raise SystemExit(main())
