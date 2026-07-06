"""Data analysis agent for one iteration."""

from __future__ import annotations

import pandas as pd

from src.config import settings
from src.llm.base import LLMProvider
from src.schemas import Constraints
from src.utils.llm_audit import LLMCallRecorder, evidence_from_raw


class DataAnalystAgent:
    """Summarizes measured results and selects the best candidate."""

    def analyze(
        self,
        iteration: int,
        results: list[dict],
        constraints: Constraints,
        previous_best: dict | None,
        problem: dict | None = None,
        llm: LLMProvider | None = None,
        recorder: LLMCallRecorder | None = None,
    ) -> dict:
        """Analyze one iteration result table."""

        df = pd.DataFrame(results)
        feasible = df[~df["constraint_violation"]].copy()
        target = constraints.target_metric
        ascending = target == "energy_cost"
        ranking_df = feasible if not feasible.empty else df
        best_row = ranking_df.sort_values(target, ascending=ascending).iloc[0].to_dict()

        failed = df[df["constraint_violation"]].copy()
        failure_reasons = []
        if not failed.empty:
            high_energy = failed[failed["energy_cost"] > constraints.max_energy_cost]
            low_stability = failed[failed["stability_score"] < constraints.min_stability]
            if not high_energy.empty:
                failure_reasons.append(
                    f"{len(high_energy)} candidates exceeded max_energy_cost={constraints.max_energy_cost}."
                )
            if not low_stability.empty:
                failure_reasons.append(
                    f"{len(low_stability)} candidates fell below min_stability={constraints.min_stability}."
                )

        improvement = None
        if previous_best:
            previous_value = float(previous_best[target])
            current_value = float(best_row[target])
            improvement = (
                previous_value - current_value
                if target == "energy_cost"
                else current_value - previous_value
            )

        trends = {
            "mean_speed_mean": round(float(df["mean_speed"].mean()), 5),
            "energy_cost_mean": round(float(df["energy_cost"].mean()), 5),
            "efficiency_mean": round(float(df["efficiency"].mean()), 5),
            "stability_score_mean": round(float(df["stability_score"].mean()), 5),
            "feasible_count": int((~df["constraint_violation"]).sum()),
            "total_count": int(len(df)),
        }

        llm_summary = ""
        evidence = None
        if llm:
            llm_summary, evidence = self._call_data_analyst_llm(
                iteration, results, constraints, best_row, failure_reasons, problem or {}, llm, recorder
            )

        return {
            "iteration": iteration,
            "target_metric": target,
            "best_candidate": best_row,
            "failure_reasons": failure_reasons or ["No hard constraint failure in this iteration."],
            "trends": trends,
            "improvement_vs_previous_best": None if improvement is None else round(float(improvement), 5),
            "summary": self._summary(best_row, trends, failure_reasons, target),
            "qwen_data_analysis": llm_summary,
            "llm_evidence": evidence,
        }

    def _summary(
        self, best_row: dict, trends: dict, failure_reasons: list[str], target: str
    ) -> str:
        """Generate a short human-readable analysis paragraph."""

        return (
            f"Best candidate is {best_row['candidate_id']} by {target}. "
            f"Mean efficiency this round is {trends['efficiency_mean']}, with "
            f"{trends['feasible_count']}/{trends['total_count']} feasible candidates. "
            f"Main issue: {failure_reasons[0] if failure_reasons else 'none'}"
        )

    def _call_data_analyst_llm(
        self,
        iteration: int,
        results: list[dict],
        constraints: Constraints,
        best_row: dict,
        failure_reasons: list[str],
        problem: dict,
        llm: LLMProvider,
        recorder: LLMCallRecorder | None,
    ) -> tuple[str, dict]:
        """Ask Qwen to summarize this iteration's data."""

        system_prompt = (
            "You are DataAnalystAgent. Summarize experiment results in concise text. "
            "Do not invent metrics."
        )
        user_prompt = (
            f"iteration={iteration}\n"
            f"constraints={constraints.model_dump()}\n"
            f"qwen_problem_analysis={problem.get('qwen_problem_analysis', problem)}\n"
            f"results={results}\n"
            f"best_candidate={best_row}\n"
            f"failure_reasons={failure_reasons}\n"
            "Return a concise paragraph explaining the trend and failure causes."
        )
        try:
            if recorder:
                record = recorder.call("DataAnalystAgent", system_prompt, user_prompt)
                return record.raw_response, record.evidence
            raw = llm.generate(system_prompt, user_prompt)
            return raw, evidence_from_raw(llm, raw)
        except Exception:
            if settings.qwen_require_real:
                raise
            return "", evidence_from_raw(llm, "")
