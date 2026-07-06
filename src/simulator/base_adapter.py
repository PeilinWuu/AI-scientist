"""Unified simulation adapter interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

import pandas as pd

from src.schemas import Constraints, ExperimentCandidate, ExperimentParams
from src.simulator.soft_swimmer_simulator import SoftSwimmerSimulator


CandidateInput = ExperimentCandidate | tuple[str, ExperimentParams]


class SimulationAdapter(ABC):
    """Common adapter interface for lightweight, FreeFlow, or CFD backends."""

    @abstractmethod
    def run_candidate(self, candidate: CandidateInput, constraints: Constraints) -> dict:
        """Run or retrieve one candidate result as a dictionary."""

    def run_batch(
        self, candidates: Iterable[CandidateInput], constraints: Constraints
    ) -> pd.DataFrame:
        """Run a batch of candidates and return a result table."""

        return pd.DataFrame(
            [self.run_candidate(candidate, constraints) for candidate in candidates]
        )


class LightweightSimulatorAdapter(SimulationAdapter):
    """Adapter wrapper around the built-in lightweight virtual simulator."""

    def __init__(self, random_seed: int = 42) -> None:
        self.random_seed = random_seed

    def run_candidate(self, candidate: CandidateInput, constraints: Constraints) -> dict:
        """Evaluate one candidate with the lightweight simulator."""

        candidate_id, params = normalize_candidate(candidate)
        simulator = SoftSwimmerSimulator(constraints, random_seed=self.random_seed)
        return simulator.evaluate(candidate_id, params, iteration=1).model_dump()

    def run_batch(
        self, candidates: Iterable[CandidateInput], constraints: Constraints
    ) -> pd.DataFrame:
        """Evaluate a batch with one simulator instance for reproducible noise order."""

        simulator = SoftSwimmerSimulator(constraints, random_seed=self.random_seed)
        pairs = [normalize_candidate(candidate) for candidate in candidates]
        results = simulator.run_batch(pairs, iteration=1)
        return pd.DataFrame([result.model_dump() for result in results])


def normalize_candidate(candidate: CandidateInput) -> tuple[str, ExperimentParams]:
    """Return a candidate id and parameter object from supported input shapes."""

    if isinstance(candidate, ExperimentCandidate):
        return candidate.candidate_id, candidate.params
    return candidate
