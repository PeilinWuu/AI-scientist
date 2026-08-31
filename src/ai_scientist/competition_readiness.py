"""Evidence-based Competition 1B submission readiness checker."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

from src.pure_qwen_client import PureQwenClient


QWEN_SMOKE_MARKER = "QWEN_SMOKE_OK"
QWEN_SMOKE_PROMPT = "Return exactly: QWEN_SMOKE_OK"
QWEN_SMOKE_EVIDENCE = "qwen_smoke_evidence.json"
DEFAULT_SMOKE_MAX_AGE_HOURS = 168


def check_readiness(root: str | Path = "competition/1b", run_tests: bool = True) -> dict[str, Any]:
    root = Path(root).resolve()
    repository = Path(__file__).resolve().parents[2]
    load_dotenv(repository / ".env", override=False)
    flagship = root / "cases/flagship"
    test_result = _run_tests(repository) if run_tests else _load_test_result(root)
    (root / "results").mkdir(parents=True, exist_ok=True)
    (root / "results/test_summary.json").write_text(
        json.dumps(test_result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    qwen_smoke = _qwen_smoke_status(root)
    streamlit_source = (repository / "app_streamlit.py").read_text(encoding="utf-8")
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
        "streamlit_demo_present": all(
            marker in streamlit_source
            for marker in ["render_research_workspace", "DAMPED_OSCILLATOR_EXAMPLE", "加载示例：阻尼振子参数辨识"]
        ) and "render_competition_demo" not in streamlit_source,
        "qwen_config_present": "DASHSCOPE_API_KEY" in (repository / ".env.example").read_text(encoding="utf-8"),
        "qwen_real_smoke_test_status": qwen_smoke["status"],
        "qwen_real_smoke_test_passed": qwen_smoke["passed"],
        "qwen_real_smoke_test_evidence": qwen_smoke["evidence"],
        "documentation_complete": _all(root / "submission", [
            "PPT_CONTENT_DRAFT.md", "SUBMISSION_REQUIREMENTS_CHECKLIST.md", "DEMO_SCRIPT.md",
            "REPRODUCE.md", "EVIDENCE_INDEX.md",
        ]),
    }
    boolean_checks = [value for value in checks.values() if isinstance(value, bool)]
    passed_count = sum(boolean_checks)
    manual = [
        "Capture Streamlit, API, and authenticated Qwen evidence screenshots after final UI review.",
        "Polish and export the <=20-page PPT/PDF.",
        "Record/upload the demo video and complete official submission forms.",
    ]
    if not qwen_smoke["passed"]:
        manual.insert(0, "Run an authenticated Qwen smoke test with --run-qwen-smoke.")
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


def run_qwen_smoke(
    root: str | Path = "competition/1b",
    client_factory: Any = PureQwenClient,
) -> dict[str, Any]:
    """Perform one paid authenticated call and persist only redacted evidence."""

    root = Path(root).resolve()
    repository = Path(__file__).resolve().parents[2]
    load_dotenv(repository / ".env", override=False)
    evidence_path = root / "results" / QWEN_SMOKE_EVIDENCE
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    verified_at = datetime.now(timezone.utc)
    model = os.getenv("LLM_MODEL", "qwen3.8-max")
    base_url = os.getenv("LLM_BASE_URL", "")
    evidence: dict[str, Any] = {
        "provider": "Alibaba Cloud Bailian / Qwen",
        "model": model,
        "base_url": _safe_base_url(base_url),
        "verified_at": verified_at.isoformat(),
        "status": "FAILED",
        "response_marker": QWEN_SMOKE_MARKER,
        "success": False,
        "error_type": None,
        "error_message": None,
        "latency_ms": None,
    }
    try:
        client = client_factory()
        evidence["model"] = str(getattr(client, "model", model))
        evidence["base_url"] = _safe_base_url(str(getattr(client, "base_url", base_url)))
        response = client.chat([{"role": "user", "content": QWEN_SMOKE_PROMPT}])
        success = response.strip() == QWEN_SMOKE_MARKER
        evidence.update(
            status="PASSED" if success else "FAILED_UNEXPECTED_RESPONSE",
            success=success,
            error_type=None if success else "UnexpectedResponse",
            error_message=None if success else "Qwen response did not exactly match the required marker.",
        )
    except Exception as exc:  # evidence records a safe diagnostic and never the secret
        evidence.update(
            status="FAILED",
            error_type=type(exc).__name__,
            error_message=_sanitize_smoke_error(str(exc)),
        )
    evidence["latency_ms"] = max(0, round((time.perf_counter() - started) * 1000))
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return evidence


def _qwen_smoke_status(root: Path, now: datetime | None = None) -> dict[str, Any]:
    path = root / "results" / QWEN_SMOKE_EVIDENCE
    relative_path = f"results/{QWEN_SMOKE_EVIDENCE}"
    if not path.exists():
        return {"status": "NOT_RUN", "passed": False, "evidence": relative_path}
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
        verified_at = datetime.fromisoformat(str(evidence["verified_at"]).replace("Z", "+00:00"))
        if verified_at.tzinfo is None:
            raise ValueError("verified_at must include a timezone")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {"status": "INVALID_EVIDENCE", "passed": False, "evidence": relative_path}
    current = now or datetime.now(timezone.utc)
    max_age_hours = max(1, int(os.getenv("AI_SCIENTIST_QWEN_SMOKE_MAX_AGE_HOURS", str(DEFAULT_SMOKE_MAX_AGE_HOURS))))
    if verified_at > current + timedelta(minutes=5):
        return {"status": "INVALID_EVIDENCE_FUTURE_TIMESTAMP", "passed": False, "evidence": relative_path}
    if current - verified_at > timedelta(hours=max_age_hours):
        return {"status": "EXPIRED", "passed": False, "evidence": relative_path}
    passed = bool(
        evidence.get("success") is True
        and evidence.get("status") == "PASSED"
        and evidence.get("response_marker") == QWEN_SMOKE_MARKER
        and evidence.get("model")
        and evidence.get("base_url")
    )
    return {
        "status": "PASSED" if passed else str(evidence.get("status") or "FAILED"),
        "passed": passed,
        "evidence": relative_path,
    }


def _sanitize_smoke_error(message: str) -> str:
    secret = os.getenv("DASHSCOPE_API_KEY", "")
    sanitized = message.replace(secret, "[REDACTED_API_KEY]") if secret else message
    sanitized = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;}]+", r"\1[REDACTED]", sanitized)
    sanitized = re.sub(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;}]+", r"\1[REDACTED]", sanitized)
    return sanitized[:1000]


def _safe_base_url(base_url: str) -> str:
    """Keep the endpoint identity while dropping credentials, query, and fragments."""

    parsed = urlsplit(base_url.strip())
    hostname = parsed.hostname or ""
    if parsed.port:
        hostname = f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme, hostname, parsed.path.rstrip("/"), "", ""))


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
    parser.add_argument(
        "--run-qwen-smoke",
        action="store_true",
        help="Make one real Qwen API call and save redacted evidence before checking readiness.",
    )
    args = parser.parse_args()
    if args.run_qwen_smoke:
        evidence = run_qwen_smoke(args.root)
        print(json.dumps({"qwen_smoke_evidence": evidence}, ensure_ascii=False, indent=2))
    result = check_readiness(args.root, run_tests=not args.skip_tests)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    required = [value for value in result.values() if isinstance(value, bool)]
    return 0 if all(required) else 1


if __name__ == "__main__":
    raise SystemExit(main())
