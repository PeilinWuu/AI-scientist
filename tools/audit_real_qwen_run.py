"""Audit whether a saved run used real Qwen calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import settings


def main() -> int:
    args = _parse_args()
    run_id = args.run_id or _latest_run_id()
    if not run_id:
        print("REAL_QWEN_RUN=false")
        print("Reason: no run directory found.")
        return 1

    run_dir = settings.runs_dir / run_id
    reasons = _audit_run(run_dir)
    if reasons:
        print("REAL_QWEN_RUN=false")
        for reason in reasons:
            print(f"- {reason}")
        return 1
    print("REAL_QWEN_RUN=true")
    print(f"run_id={run_id}")
    print(f"llm_calls={run_dir / 'llm_calls'}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit a FlowScientist real Qwen run.")
    parser.add_argument("--run-id", default=None, help="Run id to audit")
    return parser.parse_args()


def _latest_run_id() -> str | None:
    if not settings.runs_dir.exists():
        return None
    run_dirs = [path for path in settings.runs_dir.iterdir() if path.is_dir()]
    if not run_dirs:
        return None
    return max(run_dirs, key=lambda path: path.stat().st_mtime).name


def _audit_run(run_dir: Path) -> list[str]:
    reasons: list[str] = []
    metadata_path = run_dir / "metadata.json"
    config_path = run_dir / "config.json"
    metadata = _read_json(metadata_path)
    config = _read_json(config_path)

    if not metadata and not config:
        reasons.append("metadata.json and config.json are missing or unreadable.")
    audit_source = metadata or config
    if audit_source.get("is_mock") is not False:
        reasons.append("is_mock is not false.")
    if audit_source.get("llm_provider") != "qwen":
        reasons.append("llm_provider is not qwen.")
    if audit_source.get("llm_transport") != "curl":
        reasons.append("llm_transport is not curl.")

    calls_dir = run_dir / "llm_calls"
    tool_calls_dir = run_dir / "tool_calls"
    is_conversation_run = (run_dir / "conversation.json").exists()
    if not calls_dir.exists():
        reasons.append("llm_calls folder is missing.")
        return reasons

    request_files = sorted(calls_dir.glob("*_request.json"))
    response_files = sorted(calls_dir.glob("*_response.json"))
    min_llm_calls = 2 if is_conversation_run else 5
    if len(response_files) < min_llm_calls:
        reasons.append(
            f"expected at least {min_llm_calls} LLM responses, found {len(response_files)}."
        )
    if len(request_files) != len(response_files):
        reasons.append(
            f"request/response file count mismatch: {len(request_files)} vs {len(response_files)}."
        )

    response_prefixes = {path.name.replace("_response.json", "") for path in response_files}
    for request_path in request_files:
        prefix = request_path.name.replace("_request.json", "")
        if prefix not in response_prefixes:
            reasons.append(f"missing response for {request_path.name}.")

    for response_path in response_files:
        data = _read_json(response_path)
        if not str(data.get("raw_response", "")).strip():
            reasons.append(f"empty raw_response in {response_path.name}.")
        if data.get("is_mock") is not False:
            reasons.append(f"{response_path.name} is marked mock.")

    report_path = run_dir / "final_report.md"
    if not report_path.exists():
        reasons.append("final_report.md is missing.")
    elif "LLM Backend" not in report_path.read_text(encoding="utf-8"):
        reasons.append("final_report.md does not contain LLM Backend.")

    if is_conversation_run:
        if not tool_calls_dir.exists():
            reasons.append("tool_calls folder is missing.")
        elif not list(tool_calls_dir.glob("*_response.json")):
            reasons.append("conversation run has no tool call responses.")
    return reasons


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
