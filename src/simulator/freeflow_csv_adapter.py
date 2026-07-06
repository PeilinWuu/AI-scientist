"""CSV-backed adapter for future FreeFlow/CFD result integration."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.schemas import Constraints
from src.simulator.base_adapter import (
    CandidateInput,
    LightweightSimulatorAdapter,
    SimulationAdapter,
    normalize_candidate,
)


REQUIRED_COLUMNS = {
    "amplitude",
    "frequency",
    "wavelength",
    "stiffness",
    "phase",
    "mean_speed",
    "energy_cost",
    "efficiency",
    "stability_score",
    "vortex_loss",
}


class FreeFlowCSVAdapter(SimulationAdapter):
    """Read precomputed FreeFlow/CFD-like results from a CSV table.

    This adapter does not run CFD. It looks up the closest row whose parameter
    values are within ``tolerance`` of the requested candidate. If no close row
    exists, it either falls back to the lightweight simulator or raises a clear
    error.
    """

    def __init__(
        self,
        csv_path: str | Path,
        tolerance: float = 1e-3,
        fallback_to_lightweight: bool = True,
        random_seed: int = 42,
    ) -> None:
        self.csv_path = Path(csv_path)
        self.tolerance = tolerance
        self.fallback_to_lightweight = fallback_to_lightweight
        self.fallback = LightweightSimulatorAdapter(random_seed=random_seed)
        self.data = pd.read_csv(self.csv_path)
        self._validate_columns()

    def run_candidate(self, candidate: CandidateInput, constraints: Constraints) -> dict:
        """Return the closest matching CSV row or a fallback result."""

        candidate_id, params = normalize_candidate(candidate)
        param_columns = ["amplitude", "frequency", "wavelength", "stiffness", "phase"]
        target = np.array([getattr(params, column) for column in param_columns], dtype=float)
        values = self.data[param_columns].to_numpy(dtype=float)
        distances = np.max(np.abs(values - target), axis=1)
        match_index = int(np.argmin(distances)) if len(distances) else -1

        if match_index >= 0 and distances[match_index] <= self.tolerance:
            row = self.data.iloc[match_index].to_dict()
            row["candidate_id"] = candidate_id
            row["constraint_violation"] = bool(
                row["energy_cost"] > constraints.max_energy_cost
                or row["stability_score"] < constraints.min_stability
            )
            return row

        if self.fallback_to_lightweight:
            return self.fallback.run_candidate(candidate, constraints)

        raise ValueError(
            f"No close FreeFlow/CFD CSV result found for candidate {candidate_id} "
            f"within tolerance={self.tolerance}."
        )

    def _validate_columns(self) -> None:
        """Ensure the CSV contains the schema expected by the workflow."""

        missing = sorted(REQUIRED_COLUMNS - set(self.data.columns))
        if missing:
            raise ValueError(
                f"FreeFlow CSV is missing required columns: {', '.join(missing)}"
            )
