"""Deterministic two-round Competition 1B flagship runtime."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

from src.ai_scientist.competition_schemas import (
    CompetitionRunState,
    DampedOscillatorPlan,
    ExecutionRequest,
    ExecutionResult,
    FeedbackSignal,
    IterationRecord,
    PlanAdjustment,
    competition_id,
    now_utc,
)
from src.ai_scientist.tools.execution_adapter import ExecutionAdapter


class CompetitionRuntime:
    """Run and persist a fixed scientific benchmark without model-generated numbers."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.executor = ExecutionAdapter(self.root)

    def run_flagship(self, seed: int = 20260831) -> CompetitionRunState:
        self._ensure_layout()
        run_id = competition_id("flagship")
        state = CompetitionRunState(
            run_id=run_id,
            status="created",
            seed=seed,
            root_directory=str(self.root),
        )
        self._write_json("audit/provenance.json", {
            "run_id": run_id,
            "case": state.case_name,
            "seed": seed,
            "numeric_source": "controlled_local_deterministic",
            "llm_generated_metrics": False,
            "created_at": now_utc().isoformat(),
        })
        generation = self._execute(ExecutionRequest(
            operation="run_simulation",
            parameters={
                "mode": "generate_damped_oscillator", "damping": 0.173,
                "omega": 2.37, "amplitude": 1.0, "phase": 0.2,
                "noise_std": 0.03, "duration": 12.0, "samples": 360,
            },
            expected_outputs=["observations.csv", "ground_truth.json", "observations.png"],
            provenance={"case": state.case_name, "role": "synthetic_observation_generation"},
            seed=seed,
            output_directory="input",
        ))
        if generation.status != "success":
            return self._fail_state(state, generation.failure_reason or "input generation failed")
        self._event("input_generated", generation.model_dump(mode="json"))

        plan_1 = DampedOscillatorPlan(
            plan_version="round_1_v1", damping_min=0.05, damping_max=0.35,
            damping_points=7, omega_min=2.0, omega_max=2.8, omega_points=9,
            success_rmse=0.04, resource_budget_evaluations=63,
            rationale="Pre-registered broad coarse grid before observing fit results.",
        )
        self._write_json("round_1/plan.json", plan_1.model_dump(mode="json"))
        result_1 = self._execute_fit(plan_1, "round_1/execution", seed)
        if result_1.status != "success":
            return self._fail_state(state, result_1.failure_reason or "round 1 failed")
        evaluation_1 = self._evaluation(result_1, generation, plan_1)
        self._write_json("round_1/analysis/evaluation.json", evaluation_1)
        state.plans.append(plan_1)
        state.executions.extend([generation, result_1])
        state.status = "round_1_complete"
        self._event("round_1_completed", {"execution_id": result_1.execution_id, **evaluation_1})

        feedback, adjustments, plan_2, decision = self._feedback_and_next_plan(plan_1, result_1)
        self._write_json("feedback/feedback.json", feedback.model_dump(mode="json"))
        self._write_json("feedback/plan_adjustments.json", [item.model_dump(mode="json") for item in adjustments])
        iteration_1 = IterationRecord(
            iteration=1,
            plan_version=plan_1.plan_version,
            execution_ids=[result_1.execution_id],
            analysis_artifact_ids=[item.artifact_id for item in result_1.artifacts],
            feedback_signals=[feedback],
            adjustments=adjustments,
            next_plan_version=plan_2.plan_version if plan_2 else None,
            decision=decision,
        )
        state.iterations.append(iteration_1)
        self._event("feedback_generated", iteration_1.model_dump(mode="json"))
        if plan_2 is None:
            state.status = "human_review" if decision == "human_review" else "complete"
            self._save_state(state)
            return state

        self._write_json("round_2/plan.json", plan_2.model_dump(mode="json"))
        result_2 = self._execute_fit(plan_2, "round_2/execution", seed)
        if result_2.status != "success":
            return self._fail_state(state, result_2.failure_reason or "round 2 failed")
        evaluation_2 = self._evaluation(result_2, generation, plan_2)
        self._write_json("round_2/analysis/evaluation.json", evaluation_2)
        state.plans.append(plan_2)
        state.executions.append(result_2)
        state.iterations.append(IterationRecord(
            iteration=2,
            plan_version=plan_2.plan_version,
            execution_ids=[result_2.execution_id],
            analysis_artifact_ids=[item.artifact_id for item in result_2.artifacts],
            decision="stop",
        ))

        baseline_plan = DampedOscillatorPlan(
            plan_version="baseline_one_shot_v1", damping_min=plan_1.damping_min,
            damping_max=plan_1.damping_max, damping_points=14,
            omega_min=plan_1.omega_min, omega_max=plan_1.omega_max, omega_points=13,
            success_rmse=plan_1.success_rmse, resource_budget_evaluations=182,
            rationale="One-shot uniform grid using approximately the same total evaluation budget without feedback.",
        )
        self._write_json("comparison/baseline_plan.json", baseline_plan.model_dump(mode="json"))
        baseline = self._execute_fit(baseline_plan, "comparison/baseline_execution", seed)
        if baseline.status != "success":
            return self._fail_state(state, baseline.failure_reason or "baseline failed")
        state.baseline_execution = baseline
        comparison = self._comparison(plan_1, plan_2, result_1, result_2, baseline, generation)
        state.comparison = comparison
        state.status = "complete"
        state.updated_at = now_utc()
        self._write_json("comparison/iteration_comparison.json", comparison["iteration"])
        self._write_json("comparison/baseline_comparison.json", comparison["baseline"])
        self._write_json("round_2/analysis/iteration_record.json", state.iterations[1].model_dump(mode="json"))
        self._event("round_2_completed", {"execution_id": result_2.execution_id, **evaluation_2})
        self._event("run_completed", comparison)
        self._save_state(state)
        self._write_case_readme(state)
        return state

    def run_failure_cases(self) -> list[dict[str, Any]]:
        cases = [
            ("missing_file", {"operation": "inspect_dataset", "inputs": {"dataset_path": "input/not_found.csv"}}),
            ("invalid_operation", {"operation": "run_arbitrary_code", "parameters": {"code": "print('no')"}}),
            ("parameter_out_of_range", {
                "operation": "run_simulation", "parameters": {
                    "mode": "generate_damped_oscillator", "damping": -1,
                    "omega": 2.0, "amplitude": 1.0, "phase": 0.0,
                    "noise_std": 0.1, "duration": 2, "samples": 100,
                }, "output_directory": "failure_outputs/out_of_range",
            }),
            ("path_escape", {"operation": "inspect_dataset", "inputs": {"dataset_path": "../../secret.csv"}}),
        ]
        results = []
        for name, request in cases:
            result = self.executor.execute(request)
            results.append({
                "case": name,
                "detected": result["status"] in {"rejected", "failed"},
                "status": result["status"],
                "error": result["failure_reason"],
                "next_action": "human_review" if name == "path_escape" else "correct_input",
            })
        return results

    def _execute_fit(self, plan: DampedOscillatorPlan, output_directory: str, seed: int) -> ExecutionResult:
        return self._execute(ExecutionRequest(
            operation="run_simulation",
            inputs={"observations_path": "input/observations.csv"},
            parameters={
                "mode": "fit_damped_oscillator", "damping_min": plan.damping_min,
                "damping_max": plan.damping_max, "damping_points": plan.damping_points,
                "omega_min": plan.omega_min, "omega_max": plan.omega_max,
                "omega_points": plan.omega_points, "amplitude": 1.0, "phase": 0.2,
            },
            expected_outputs=["fit_result.json", "fit_grid.csv", "fit.png"],
            provenance={"plan_version": plan.plan_version, "derived_from_execution_id": plan.derived_from_execution_id},
            seed=seed,
            output_directory=output_directory,
        ))

    def _execute(self, request: ExecutionRequest) -> ExecutionResult:
        result = ExecutionResult.model_validate(self.executor.execute(request))
        request_path = f"{request.output_directory}/execution_request.json"
        result_path = f"{request.output_directory}/execution_result.json"
        self._write_json(request_path, request.model_dump(mode="json"))
        self._write_json(result_path, result.model_dump(mode="json"))
        return result

    def _feedback_and_next_plan(self, plan, result):
        metrics = result.metrics
        artifact_ids = [item.artifact_id for item in result.artifacts]
        flags = []
        if metrics["best_on_boundary"]:
            flags.append("best_parameter_on_search_boundary")
        if metrics["rmse"] > plan.success_rmse:
            flags.append("rmse_above_success_threshold")
        feedback = FeedbackSignal(
            source_artifact_ids=artifact_ids,
            observed_result={
                "rmse": metrics["rmse"], "best_damping": metrics["best_damping"],
                "best_omega": metrics["best_omega"], "evaluations": metrics["evaluations"],
            },
            expected_result={"rmse_lte": plan.success_rmse},
            quality_flags=flags,
            trigger="round_1_deterministic_fit_evaluation",
            confidence=1.0,
        )
        if metrics["best_on_boundary"]:
            adjustment = PlanAdjustment(
                target_task_id=plan.task_id, field="search_bounds",
                old_value={"damping": [plan.damping_min, plan.damping_max], "omega": [plan.omega_min, plan.omega_max]},
                new_value=None,
                reason="Best fit lies on a pre-registered boundary; automatic narrowing could exclude the optimum.",
                evidence_refs=artifact_ids, decision="human_review",
            )
            return feedback, [adjustment], None, "human_review"
        damping_half_width = metrics["damping_step"]
        omega_half_width = metrics["omega_step"]
        new_values = {
            "damping": [max(0.001, metrics["best_damping"] - damping_half_width), metrics["best_damping"] + damping_half_width],
            "omega": [max(0.01, metrics["best_omega"] - omega_half_width), metrics["best_omega"] + omega_half_width],
            "points": [11, 11],
        }
        adjustments = [
            PlanAdjustment(
                target_task_id=plan.task_id, field="search_bounds_and_resolution",
                old_value={
                    "damping": [plan.damping_min, plan.damping_max],
                    "omega": [plan.omega_min, plan.omega_max],
                    "points": [plan.damping_points, plan.omega_points],
                },
                new_value=new_values,
                reason="Round 1 located an interior optimum; refine one coarse step around the observed best fit.",
                evidence_refs=artifact_ids, decision="modify",
            )
        ]
        plan_2 = DampedOscillatorPlan(
            plan_version="round_2_v1", damping_min=new_values["damping"][0], damping_max=new_values["damping"][1],
            damping_points=11, omega_min=new_values["omega"][0], omega_max=new_values["omega"][1],
            omega_points=11, success_rmse=plan.success_rmse, resource_budget_evaluations=121,
            derived_from_execution_id=result.execution_id,
            rationale="Deterministic feedback refinement around the Round 1 observed optimum.",
        )
        return feedback, adjustments, plan_2, "continue"

    @staticmethod
    def _evaluation(result, generation, plan):
        return {
            "status": result.status,
            "rmse": result.metrics["rmse"],
            "success_threshold": plan.success_rmse,
            "threshold_met": result.metrics["rmse"] <= plan.success_rmse,
            "best_damping": result.metrics["best_damping"],
            "best_omega": result.metrics["best_omega"],
            "evaluations": result.metrics["evaluations"],
            "source_execution_id": result.execution_id,
            "source_observation_execution_id": generation.execution_id,
        }

    @staticmethod
    def _comparison(plan_1, plan_2, result_1, result_2, baseline, generation):
        truth = {"damping": 0.173, "omega": 2.37}
        error = lambda result: abs(result.metrics["best_damping"] - truth["damping"]) + abs(result.metrics["best_omega"] - truth["omega"])
        rmse_gain = result_1.metrics["rmse"] - result_2.metrics["rmse"]
        baseline_gain = baseline.metrics["rmse"] - result_2.metrics["rmse"]
        return {
            "iteration": {
                "round_1_rmse": result_1.metrics["rmse"], "round_2_rmse": result_2.metrics["rmse"],
                "absolute_rmse_gain": rmse_gain,
                "relative_rmse_gain_percent": 100 * rmse_gain / result_1.metrics["rmse"],
                "round_1_parameter_l1_error": error(result_1), "round_2_parameter_l1_error": error(result_2),
                "round_1_evaluations": plan_1.resource_budget_evaluations,
                "round_2_evaluations": plan_2.resource_budget_evaluations,
                "limitation": "Round 2 adds execution cost and remains sensitive to the assumed oscillator model.",
            },
            "baseline": {
                "iterative_final_rmse": result_2.metrics["rmse"], "one_shot_baseline_rmse": baseline.metrics["rmse"],
                "iterative_advantage_rmse": baseline_gain,
                "iterative_parameter_l1_error": error(result_2), "baseline_parameter_l1_error": error(baseline),
                "iterative_total_evaluations": plan_1.resource_budget_evaluations + plan_2.resource_budget_evaluations,
                "baseline_total_evaluations": baseline.metrics["evaluations"],
                "constraint_satisfied": result_2.metrics["rmse"] <= plan_2.success_rmse,
            },
            "provenance": {
                "observation_execution_id": generation.execution_id,
                "round_1_execution_id": result_1.execution_id,
                "round_2_execution_id": result_2.execution_id,
                "baseline_execution_id": baseline.execution_id,
            },
        }

    def _ensure_layout(self):
        for directory in (
            "input", "round_1/execution", "round_1/analysis", "feedback",
            "round_2/execution", "round_2/analysis", "comparison/figures",
            "comparison/baseline_execution", "audit",
        ):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        (self.root / "audit/event_log_excerpt.jsonl").write_text("", encoding="utf-8")

    def _event(self, event_type: str, payload: dict[str, Any]):
        event = {"event_id": competition_id("event"), "event_type": event_type, "timestamp": now_utc().isoformat(), "payload": payload}
        with (self.root / "audit/event_log_excerpt.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def _write_json(self, relative_path: str, payload: Any):
        path = (self.root / relative_path).resolve()
        path.relative_to(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def _save_state(self, state):
        state.updated_at = now_utc()
        self._write_json("audit/run_state.json", state.model_dump(mode="json"))

    def _fail_state(self, state, reason):
        state.status = "failed"
        state.comparison = {"failure_reason": reason}
        self._event("run_failed", state.comparison)
        self._save_state(state)
        return state

    def _write_case_readme(self, state):
        values = state.comparison
        content = f"""# Flagship Case — Damped Oscillator Parameter Identification

Seed: `{state.seed}`
Status: `{state.status}`

Round 1 RMSE: `{values['iteration']['round_1_rmse']:.8f}`
Round 2 RMSE: `{values['iteration']['round_2_rmse']:.8f}`
One-shot baseline RMSE: `{values['baseline']['one_shot_baseline_rmse']:.8f}`

Round 2 reads the Round 1 execution result, narrows both parameter ranges around the observed
interior optimum, and records the old/new bounds in `feedback/plan_adjustments.json`. All numeric
values were computed by the controlled local executor. The known limitation is that refinement adds
execution cost and assumes the damped-oscillator model is correctly specified.
"""
        (self.root / "README.md").write_text(content, encoding="utf-8")


def run_benchmark(output_root: str | Path, seeds: list[int] | None = None) -> dict[str, Any]:
    output_root = Path(output_root).resolve()
    seeds = seeds or [20260831, 20260832, 20260833, 20260834, 20260835]
    flagship = CompetitionRuntime(output_root / "cases/flagship").run_flagship(seeds[0])
    records = []
    for seed in seeds:
        state = CompetitionRuntime(output_root / f"results/seed_runs/{seed}").run_flagship(seed)
        records.append({"seed": seed, "status": state.status, **state.comparison.get("iteration", {}), **state.comparison.get("baseline", {})})
    complete = [item for item in records if item["status"] == "complete"]
    summary = {
        "case": "damped_oscillator_parameter_identification",
        "seeds": records,
        "completed_runs": len(complete),
        "requested_runs": len(seeds),
        "aggregate": {
            "round_1_rmse_mean": mean(item["round_1_rmse"] for item in complete),
            "round_1_rmse_std": pstdev(item["round_1_rmse"] for item in complete),
            "round_2_rmse_mean": mean(item["round_2_rmse"] for item in complete),
            "round_2_rmse_std": pstdev(item["round_2_rmse"] for item in complete),
            "baseline_rmse_mean": mean(item["one_shot_baseline_rmse"] for item in complete),
            "iterative_win_count": sum(item["iterative_final_rmse"] < item["one_shot_baseline_rmse"] for item in complete),
        } if complete else {},
        "flagship_run_id": flagship.run_id,
        "generated_at": now_utc().isoformat(),
    }
    results_dir = output_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "benchmark_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    aggregate = summary["aggregate"]
    (results_dir / "benchmark_summary.md").write_text(
        "# Benchmark Summary\n\n"
        f"Completed seeds: {summary['completed_runs']}/{summary['requested_runs']}\n\n"
        f"- Round 1 RMSE mean: {aggregate.get('round_1_rmse_mean', float('nan')):.8f}\n"
        f"- Round 2 RMSE mean: {aggregate.get('round_2_rmse_mean', float('nan')):.8f}\n"
        f"- One-shot baseline RMSE mean: {aggregate.get('baseline_rmse_mean', float('nan')):.8f}\n"
        f"- Iterative wins: {aggregate.get('iterative_win_count', 0)}/{summary['completed_runs']}\n",
        encoding="utf-8",
    )
    failures = CompetitionRuntime(output_root / "cases/flagship").run_failure_cases()
    (results_dir / "failure_cases.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (results_dir / "failure_cases.md").write_text(
        "# Failure Cases\n\n" + "\n".join(
            f"- **{item['case']}**: `{item['status']}` — {item['error']} → `{item['next_action']}`"
            for item in failures
        ) + "\n",
        encoding="utf-8",
    )
    return summary
