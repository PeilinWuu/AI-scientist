"""Lightweight virtual experiment backend for a soft swimming robot."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from src.schemas import Constraints, ExperimentParams, SimulationResult


@dataclass
class SoftSwimmerSimulator:
    """Low-cost surrogate simulator with controllable noise.

    The equations are intentionally simple. They preserve qualitative behavior:
    higher frequency increases speed and energy, excessive amplitude hurts
    stability, and medium stiffness is usually best.
    """

    constraints: Constraints
    random_seed: int = 42

    def __post_init__(self) -> None:
        self.rng = np.random.default_rng(self.random_seed)

    def run_batch(
        self, candidates: list[tuple[str, ExperimentParams]], iteration: int
    ) -> list[SimulationResult]:
        """Evaluate a list of candidates for one iteration."""

        results: list[SimulationResult] = []
        for candidate_id, params in candidates:
            results.append(self.evaluate(candidate_id, params, iteration))
        return results

    def evaluate(
        self, candidate_id: str, params: ExperimentParams, iteration: int
    ) -> SimulationResult:
        """Evaluate one parameter set and return virtual measurements."""

        p = self._clip_params(params)
        noise = self.rng.normal(0.0, 0.015)

        amp_effect = 1.0 - math.exp(-7.0 * p.amplitude)
        freq_effect = math.log1p(1.25 * p.frequency)
        wavelength_effect = math.exp(-((p.wavelength - 1.15) ** 2) / 0.45)
        stiffness_effect = math.exp(-((p.stiffness - 0.48) ** 2) / 0.16)
        phase_effect = 0.92 + 0.08 * math.cos(2 * math.pi * (p.phase - 0.22))

        mean_speed = (
            0.16
            + 1.25
            * amp_effect
            * freq_effect
            * wavelength_effect
            * (0.62 + 0.38 * stiffness_effect)
            * phase_effect
        )
        mean_speed *= 1.0 + noise

        energy_cost = (
            0.18
            + 1.75 * (p.amplitude**2) * (p.frequency**2.15)
            + 0.28 * abs(p.stiffness - 0.42)
            + 0.10 * (1.0 / max(p.wavelength, 0.05))
        )
        energy_cost *= 1.0 + self.rng.normal(0.0, 0.02)

        low_stiffness_loss = max(0.0, 0.32 - p.stiffness) * 0.9
        high_stiffness_loss = max(0.0, p.stiffness - 0.72) * 0.45
        excessive_amplitude_loss = max(0.0, p.amplitude - 0.32) * 1.25
        high_frequency_loss = max(0.0, p.frequency - 2.25) * 0.12
        stability_score = (
            0.95
            - low_stiffness_loss
            - high_stiffness_loss
            - excessive_amplitude_loss
            - high_frequency_loss
        )
        stability_score += self.rng.normal(0.0, 0.012)
        stability_score = float(np.clip(stability_score, 0.0, 1.0))

        vortex_loss = (
            0.08
            + 0.35 * max(0.0, p.amplitude - 0.25)
            + 0.11 * abs(p.phase - 0.25)
            + 0.18 * abs(p.wavelength - 1.1)
            + 0.08 * max(0.0, p.frequency - 2.2)
        )
        vortex_loss = float(np.clip(vortex_loss + self.rng.normal(0.0, 0.01), 0.0, 1.0))

        usable_speed = mean_speed * (0.65 + 0.35 * stability_score) * (1.0 - 0.35 * vortex_loss)
        efficiency = usable_speed / max(energy_cost, 1e-6)

        constraint_violation = (
            energy_cost > self.constraints.max_energy_cost
            or stability_score < self.constraints.min_stability
        )

        return SimulationResult(
            candidate_id=candidate_id,
            amplitude=round(p.amplitude, 4),
            frequency=round(p.frequency, 4),
            wavelength=round(p.wavelength, 4),
            stiffness=round(p.stiffness, 4),
            phase=round(p.phase, 4),
            mean_speed=round(float(mean_speed), 5),
            energy_cost=round(float(energy_cost), 5),
            efficiency=round(float(efficiency), 5),
            stability_score=round(stability_score, 5),
            vortex_loss=round(vortex_loss, 5),
            constraint_violation=bool(constraint_violation),
        )

    def _clip_params(self, params: ExperimentParams) -> ExperimentParams:
        """Keep parameters inside simulator-supported bounds."""

        return ExperimentParams(
            amplitude=float(np.clip(params.amplitude, *self.constraints.amplitude_range)),
            frequency=float(np.clip(params.frequency, *self.constraints.frequency_range)),
            wavelength=float(np.clip(params.wavelength, *self.constraints.wavelength_range)),
            stiffness=float(np.clip(params.stiffness, *self.constraints.stiffness_range)),
            phase=float(np.clip(params.phase, *self.constraints.phase_range)),
        )
