"""Matplotlib visualization tool for experiment history."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from src.tools.base import Tool
from src.utils.io import ensure_dir


class PlotTool(Tool):
    """Generate experiment plots from current conversation history."""

    name = "generate_experiment_plot"
    description = "Generate plots from experiment history or provided tabular result data."
    schema = {
        "type": "object",
        "properties": {
            "figure_type": {
                "type": "string",
                "enum": [
                    "efficiency_by_candidate",
                    "speed_energy_scatter",
                    "stability_efficiency_scatter",
                    "parameter_trend",
                    "metric_trend",
                ],
            },
            "experiment_history": {"type": "array"},
            "run_dir": {"type": "string"},
        },
        "required": ["figure_type"],
    }

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        figure_type = arguments.get("figure_type") or "efficiency_by_candidate"
        run_dir = Path(arguments.get("run_dir") or "runs/adhoc_plot")
        figures_dir = run_dir / "figures"
        ensure_dir(figures_dir)

        df = _history_to_frame(arguments.get("experiment_history") or [])
        if df.empty:
            raise ValueError("No experiment results are available for plotting.")

        figure_path = figures_dir / f"{figure_type}.png"
        plt.figure(figsize=(7.2, 4.4))

        if figure_type == "efficiency_by_candidate":
            df.plot(kind="bar", x="candidate_id", y="efficiency", legend=False, ax=plt.gca())
            plt.ylabel("Efficiency")
            plt.xlabel("Candidate")
            caption = "Candidate efficiency comparison."
        elif figure_type == "speed_energy_scatter":
            plt.scatter(df["energy_cost"], df["mean_speed"], c=df["efficiency"], cmap="viridis")
            plt.colorbar(label="Efficiency")
            plt.xlabel("Energy cost")
            plt.ylabel("Mean speed")
            caption = "Speed-energy trade-off scatter plot."
        elif figure_type == "stability_efficiency_scatter":
            plt.scatter(df["stability_score"], df["efficiency"], c=df["energy_cost"], cmap="plasma")
            plt.colorbar(label="Energy cost")
            plt.xlabel("Stability score")
            plt.ylabel("Efficiency")
            caption = "Stability-efficiency scatter plot."
        elif figure_type == "parameter_trend":
            grouped = df.groupby("iteration", as_index=False)[
                ["amplitude", "frequency", "wavelength", "stiffness", "phase"]
            ].mean()
            for column in ["amplitude", "frequency", "wavelength", "stiffness", "phase"]:
                plt.plot(grouped["iteration"], grouped[column], marker="o", label=column)
            plt.xlabel("Iteration")
            plt.ylabel("Mean parameter value")
            plt.legend()
            caption = "Parameter trend across experiment rounds."
        elif figure_type == "metric_trend":
            grouped = df.groupby("iteration", as_index=False).agg(
                efficiency=("efficiency", "max"),
                mean_speed=("mean_speed", "max"),
                energy_cost=("energy_cost", "min"),
            )
            for column in ["efficiency", "mean_speed", "energy_cost"]:
                plt.plot(grouped["iteration"], grouped[column], marker="o", label=column)
            plt.xlabel("Iteration")
            plt.ylabel("Best metric value")
            plt.legend()
            caption = "Best metric trend across experiment rounds."
        else:
            raise ValueError(f"Unsupported figure_type: {figure_type}")

        plt.title(caption)
        plt.tight_layout()
        plt.savefig(figure_path, dpi=150)
        plt.close()

        best = df.sort_values("efficiency", ascending=False).iloc[0].to_dict()
        summary = (
            f"Generated {figure_type} from {len(df)} candidate rows. "
            f"Best efficiency candidate is {best.get('candidate_id')} "
            f"with efficiency={float(best.get('efficiency', 0.0)):.4f}."
        )
        return {
            "tool_name": self.name,
            "figure_path": str(figure_path),
            "figure_type": figure_type,
            "caption": caption,
            "summary": summary,
        }


def _history_to_frame(history: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for iteration, item in enumerate(history, start=1):
        if not isinstance(item, dict):
            continue
        for result in item.get("results", []):
            if isinstance(result, dict):
                row = dict(result)
                row.setdefault("iteration", iteration)
                rows.append(row)
    return pd.DataFrame(rows)
