"""Run or initialize AI Scientist benchmark records.

By default this script creates projects and writes benchmark_result.json without
running costly model stages. Pass --run-to-completion to advance projects until
COMPLETED or a blocking phase/error is reached.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BENCHMARK_DIR = ROOT / "data" / "benchmarks"

from src.ai_scientist.orchestrator import ResearchOrchestrator  # noqa: E402
from src.ai_scientist.schemas import ResearchPhase  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-to-completion", action="store_true")
    parser.add_argument("--max-steps", type=int, default=20)
    args = parser.parse_args()

    orchestrator = ResearchOrchestrator()
    for config_path in sorted(BENCHMARK_DIR.glob("*.json")):
        if config_path.name == "benchmark_result.json":
            continue
        result = run_one(orchestrator, config_path, args.run_to_completion, args.max_steps)
        output_path = config_path.with_name(f"{config_path.stem}_benchmark_result.json")
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"{config_path.name}: {output_path}")


def run_one(
    orchestrator: ResearchOrchestrator,
    config_path: Path,
    run_to_completion: bool,
    max_steps: int,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    started = time.time()
    errors: list[str] = []
    stage_durations: dict[str, float] = {}
    project = orchestrator.create_project(
        objective=config["objective"],
        domain_hint=config.get("domain_hint"),
        constraints=config.get("constraints", {}),
        planning_only=True,
    )

    if run_to_completion:
        for _ in range(max_steps):
            if project.phase == ResearchPhase.HUMAN_APPROVAL:
                project = orchestrator.approve_project(project.project_id)
            if project.phase in {ResearchPhase.COMPLETED, ResearchPhase.FAILED, ResearchPhase.CANCELLED}:
                break
            phase = project.phase.value
            step_started = time.time()
            try:
                orchestrator.run_next_step(project.project_id)
            except Exception as exc:  # noqa: BLE001 - benchmark result must capture failures.
                errors.append(f"{type(exc).__name__}: {exc}")
                break
            stage_durations[phase] = round(time.time() - step_started, 3)
            project = orchestrator.get_project(project.project_id)

    project = orchestrator.get_project(project.project_id)
    fallback_count = sum(1 for event in orchestrator.list_events(project.project_id) if event.fallback_used)
    return {
        "project_id": project.project_id,
        "completion_status": project.phase.value,
        "total_duration": round(time.time() - started, 3),
        "stage_durations": stage_durations,
        "total_model_calls": project.budget.used_model_calls,
        "fallback_count": fallback_count,
        "structured_retry_count": max(0, project.budget.attempted_model_calls - project.budget.successful_model_calls),
        "evidence_count": len(project.evidence),
        "evidence_coverage": project.quality_metrics.evidence_coverage,
        "hypothesis_completeness": project.quality_metrics.hypothesis_completeness,
        "conclusion_traceability": project.quality_metrics.conclusion_traceability,
        "reviewer_scores": project.reviews[-1].model_dump(mode="json") if project.reviews else None,
        "final_decision": project.reviews[-1].decision if project.reviews else None,
        "errors": errors,
    }


if __name__ == "__main__":
    main()
