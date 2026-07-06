"""Run bundled FlowScientist-Loop examples."""

from __future__ import annotations

import json
from pathlib import Path

from src.llm import get_llm_status
from src.schemas import RunRequest
from src.workflow.experiment_loop import run_experiment_loop


EXAMPLE_DIR = Path("examples")


def main() -> None:
    """Run all example cases and print report paths."""

    llm_status = get_llm_status()
    if llm_status["qwen_require_real"] and llm_status["mock_mode"]:
        raise SystemExit(
            "Real Qwen is required. Mock fallback is disabled. "
            "Set LLM_PROVIDER=qwen and QWEN_TRANSPORT=curl."
        )

    cases = [
        "case_efficiency_first.json",
        "case_low_energy.json",
        "case_human_feedback.json",
    ]
    for case_name in cases:
        data = json.loads((EXAMPLE_DIR / case_name).read_text(encoding="utf-8"))
        summary = run_experiment_loop(RunRequest(**data))
        print(f"{case_name}: run_id={summary.run_id}")
        print(f"  report={summary.final_report_path}")


if __name__ == "__main__":
    main()
