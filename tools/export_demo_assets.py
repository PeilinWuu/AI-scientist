"""Export PPT-friendly demo assets from a saved FlowScientist run."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from matplotlib import pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import settings
from src.workflow.experiment_loop import get_run


def main() -> int:
    """Export markdown, CSV, and chart assets for one run."""

    args = _parse_args()
    run_id = args.run_id or _latest_run_id(settings.runs_dir)
    if not run_id:
        print("No run found. Run python run_examples.py first.")
        return 1

    run = get_run(run_id)
    out_dir = PROJECT_ROOT / "docs" / "demo_assets" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_config_summary(out_dir, run)
    iteration_summary = _build_iteration_summary(run)
    iteration_summary.to_csv(out_dir / "02_iteration_summary.csv", index=False)
    _write_efficiency_curve(out_dir, iteration_summary)
    _write_best_candidate_table(out_dir, run)
    _write_agent_workflow_summary(out_dir)
    _write_screenshot_checklist(out_dir, run_id)

    print(f"Exported demo assets for run_id={run_id}")
    print(f"Output directory: {out_dir}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export demo assets from a run.")
    parser.add_argument("--run_id", help="Specific run id to export", default=None)
    return parser.parse_args()


def _latest_run_id(runs_dir: Path) -> str | None:
    if not runs_dir.exists():
        return None
    run_dirs = [path for path in runs_dir.iterdir() if path.is_dir()]
    if not run_dirs:
        return None
    return max(run_dirs, key=lambda path: path.stat().st_mtime).name


def _write_config_summary(out_dir: Path, run: dict) -> None:
    config = run["config"]
    constraints = config["constraints"]
    text = f"""# Run Configuration Summary

- Run ID: `{run['run_id']}`
- Research goal: {config['research_goal']}
- Max iterations: {config['max_iterations']}
- Human feedback: {config.get('human_feedback') or 'None'}
- Target metric: {constraints['target_metric']}
- Minimum stability: {constraints['min_stability']}
- Maximum energy cost: {constraints['max_energy_cost']}

PPT message: This run is configured as a feedback-driven experiment loop, not a one-shot hypothesis generation task.
"""
    (out_dir / "01_config_summary.md").write_text(text, encoding="utf-8")


def _build_iteration_summary(run: dict) -> pd.DataFrame:
    rows = []
    for item in run["history"]:
        best = item["analysis"]["best_candidate"]
        rows.append(
            {
                "iteration": item["iteration"],
                "strategy": item["plan"]["strategy"],
                "best_candidate_id": best["candidate_id"],
                "best_efficiency": best["efficiency"],
                "best_mean_speed": best["mean_speed"],
                "best_energy_cost": best["energy_cost"],
                "best_stability_score": best["stability_score"],
                "feedback": item["feedback"]["next_strategy"],
            }
        )
    return pd.DataFrame(rows)


def _write_efficiency_curve(out_dir: Path, iteration_summary: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(
        iteration_summary["iteration"],
        iteration_summary["best_efficiency"],
        marker="o",
        linewidth=2,
    )
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Best efficiency")
    ax.set_title("Best Efficiency Across Feedback Iterations")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "03_best_efficiency_curve.png", dpi=180)
    plt.close(fig)


def _write_best_candidate_table(out_dir: Path, run: dict) -> None:
    report = run["final_report_json"] or {}
    best = report.get("best_candidate") or {}
    pd.DataFrame([best]).to_csv(out_dir / "04_best_candidate_table.csv", index=False)


def _write_agent_workflow_summary(out_dir: Path) -> None:
    text = """# Agent Workflow Summary

1. ProblemAnalystAgent parses the goal, variables, constraints, and user feedback.
2. ExperimentPlannerAgent creates concrete parameter candidates for the next virtual experiment.
3. SoftSwimmerSimulator executes the lightweight virtual experiment backend.
4. DataAnalystAgent ranks candidates, finds the best design, and summarizes failures.
5. CriticAgent converts measured failures into next-round planning instructions.
6. ReportWriterAgent produces the final Markdown and JSON report.

PPT message: The main contribution is the closed-loop planning and feedback mechanism for direction 1B.
"""
    (out_dir / "05_agent_workflow_summary.md").write_text(text, encoding="utf-8")


def _write_screenshot_checklist(out_dir: Path, run_id: str) -> None:
    text = f"""# PPT Screenshot Checklist

- Streamlit run page with goal, constraints, iteration tables, and final report.
- FastAPI `/docs` page showing available endpoints.
- `runs/{run_id}/` folder structure.
- `iteration_2_plan.json` showing next-round planning.
- `iteration_2_feedback.json` showing critique-to-plan logic.
- `final_report.md` showing the generated report.
- `03_best_efficiency_curve.png` showing feedback iteration progress.
- `src/workflow/experiment_loop.py` showing closed-loop orchestration.
- `src/agents/experiment_planner.py` showing feedback-aware planning rules.
- `src/simulator/freeflow_csv_adapter.py` showing future FreeFlow/CFD adapter path.
"""
    (out_dir / "06_ppt_screenshot_checklist.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
