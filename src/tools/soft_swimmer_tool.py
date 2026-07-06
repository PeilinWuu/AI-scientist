"""Tool wrapper around the lightweight soft-swimmer simulator."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.schemas import Constraints, ExperimentParams
from src.simulator.soft_swimmer_simulator import SoftSwimmerSimulator
from src.tools.base import Tool


class SoftSwimmerExperimentTool(Tool):
    """Run a virtual soft-swimmer experiment batch."""

    name = "run_soft_swimmer_experiment"
    description = (
        "Evaluate soft-swimmer candidate parameters and return speed, energy, "
        "efficiency, stability, vortex loss, and constraint violation."
    )
    schema = {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "amplitude": {"type": "number"},
                        "frequency": {"type": "number"},
                        "wavelength": {"type": "number"},
                        "stiffness": {"type": "number"},
                        "phase": {"type": "number"},
                    },
                    "required": [
                        "candidate_id",
                        "amplitude",
                        "frequency",
                        "wavelength",
                        "stiffness",
                        "phase",
                    ],
                },
            },
            "constraints": {"type": "object"},
            "random_seed": {"type": "integer"},
        },
        "required": ["candidates"],
    }

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        constraints = Constraints(**arguments.get("constraints", {}))
        simulator = SoftSwimmerSimulator(
            constraints, random_seed=int(arguments.get("random_seed", 42))
        )
        pairs = []
        for index, item in enumerate(arguments.get("candidates", []), start=1):
            candidate_id = item.get("candidate_id") or f"candidate_{index}"
            params = ExperimentParams(
                amplitude=float(item["amplitude"]),
                frequency=float(item["frequency"]),
                wavelength=float(item["wavelength"]),
                stiffness=float(item["stiffness"]),
                phase=float(item["phase"]),
            )
            pairs.append((candidate_id, params))
        results = [result.model_dump() for result in simulator.run_batch(pairs, iteration=1)]
        df = pd.DataFrame(results)
        best = {}
        if not df.empty:
            feasible = df[~df["constraint_violation"]]
            ranked = feasible if not feasible.empty else df
            best = ranked.sort_values("efficiency", ascending=False).iloc[0].to_dict()
        return {
            "tool_name": self.name,
            "results": results,
            "best_candidate": best,
            "summary": {
                "num_candidates": len(results),
                "feasible_count": int((~df["constraint_violation"]).sum()) if not df.empty else 0,
                "best_efficiency": float(best.get("efficiency", 0.0)) if best else None,
            },
        }
