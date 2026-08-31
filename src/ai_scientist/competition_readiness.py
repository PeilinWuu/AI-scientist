"""Evidence-based Competition 1B submission readiness checker."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def check_readiness(root: str | Path = "competition/1b", run_tests: bool = True) -> dict[str, Any]:
    root = Path(root).resolve()
    repository = Path(__file__).resolve().parents[2]
    flagship = root / "cases/flagship"
    test_result = _run_tests(repository) if run_tests else _load_test_result(root)
    (root / "results").mkdir(parents=True, exist_ok=True)
    (root / "results/test_summary.json").write_text(
        json.dumps(test_result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    qwen_smoke = _qwen_smoke_status(root)
    checks = {
        "tests_passed": test_result.get("passed", False),
        "flagship_case_complete": _json_value(flagship / "audit/run_state.json", "status") == "complete",
        "round_1_artifacts_present": _all(flagship, ["round_1/plan.json", "round_1/execution/execution_result.json", "round_1/analysis/evaluation.json"]),
        "feedback_present": _all(flagship, ["feedback/feedback.json", "feedback/plan_adjustments.json"]),
        "round_2_artifacts_present": _all(flagship, ["round_2/plan.json", "round_2/execution/execution_result.json", "round_2/analysis/evaluation.json"]),
        "baseline_present": _all(flagship, ["comparison/baseline_plan.json", "comparison/baseline_execution/execution_result.json"]),
        "comparison_present": _all(root, ["results/benchmark_summary.json", "cases/flagship/comparison/baseline_comparison.json"]),
        "failure_cases_present": _valid_failure_cases(root / "results/failure_cases.json"),
        "api_demo_present": (root / "API_DEMO.md").exists() and (repository / "src/ai_scientist/competition_api.py").exists(),
        "streamlit_demo_present": "Competition Demo" in (repository / "app_streamlit.py").read_text(encoding="utf-8"),
        "qwen_config_present": "DASHSCOPE_API_KEY" in (repository / ".env.example").read_text(encoding="utf-8"),
        "qwen_real_smoke_test_status": qwen_smoke,
        "qwen_real_smoke_test_passed": qwen_smoke == "PASSED",
        "documentation_complete": _all(root / "submission", [
            "PPT_CONTENT_DRAFT.md", "SUBMISSION_REQUIREMENTS_CHECKLIST.md", "DEMO_SCRIPT.md",
            "REPRODUCE.md", "EVIDENCE_INDEX.md",
        ]),
    }
    boolean_checks = [value for value in checks.values() if isinstance(value, bool)]
    passed_count = sum(boolean_checks)
    manual = [
        "Capture real Qwen/DashScope call evidence with the competition credential.",
        "Capture Streamlit and API screenshots after final UI review.",
        "Polish and export the <=20-page PPT/PDF.",
        "Record/upload the demo video and complete official submission forms.",
    ]
    result = {
        **checks,
        "readiness_percent": round(100 * passed_count / max(len(boolean_checks), 1), 1),
        "test_summary": test_result,
        "unresolved_manual_items": manual,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "submission_readiness.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _run_tests(repository: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    output = (completed.stdout + completed.stderr).strip()
    return {"passed": completed.returncode == 0, "exit_code": completed.returncode, "output": output[-4000:]}


def _load_test_result(root: Path) -> dict[str, Any]:
    path = root / "results/test_summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"passed": False, "status": "NOT_RUN"}


def _qwen_smoke_status(root: Path) -> str:
    path = root / "results/qwen_smoke_test.json"
    if path.exists():
        if _json_value(path, "status") == "ok":
            return "PASSED"
        if _json_value(path, "error_type") == "AuthenticationError":
            return "BLOCKED_EXTERNAL_INVALID_CREDENTIAL"
        return "FAILED"
    return "NOT_RUN" if os.getenv("DASHSCOPE_API_KEY") else "BLOCKED_EXTERNAL"


def _all(root: Path, paths: list[str]) -> bool:
    return all((root / path).is_file() and (root / path).stat().st_size > 0 for path in paths)


def _json_value(path: Path, key: str) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(key)
    except (OSError, json.JSONDecodeError):
        return None


def _valid_failure_cases(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        cases = json.loads(path.read_text(encoding="utf-8"))
        return len(cases) >= 3 and all(item.get("detected") and item.get("status") in {"rejected", "failed"} for item in cases)
    except (OSError, json.JSONDecodeError, TypeError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="competition/1b")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    result = check_readiness(args.root, run_tests=not args.skip_tests)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    required = [value for value in result.values() if isinstance(value, bool)]
    return 0 if all(required) else 1


if __name__ == "__main__":
    raise SystemExit(main())
