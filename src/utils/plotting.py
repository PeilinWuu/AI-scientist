"""Plot helpers used by Streamlit and demos."""

from __future__ import annotations

import pandas as pd
from matplotlib import pyplot as plt


def plot_efficiency_history(iteration_results: list[pd.DataFrame]):
    """Return a matplotlib figure showing best efficiency by iteration."""

    iterations = []
    best_values = []
    for idx, df in enumerate(iteration_results, start=1):
        iterations.append(idx)
        best_values.append(float(df["efficiency"].max()))

    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.plot(iterations, best_values, marker="o", linewidth=2)
    ax.set_xlabel("Iteration")
    ax.set_ylabel("Best efficiency")
    ax.set_title("Efficiency improvement over feedback iterations")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig
