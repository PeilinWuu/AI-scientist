"""CLI for reproducing Competition 1B artifacts."""

from __future__ import annotations

import argparse
import json

from src.ai_scientist.competition_runtime import run_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic Competition 1B benchmark.")
    parser.add_argument("command", choices=["run-flagship"])
    parser.add_argument("--output", default="competition/1b")
    parser.add_argument("--seeds", nargs="*", type=int)
    args = parser.parse_args()
    summary = run_benchmark(args.output, args.seeds)
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary["completed_runs"] == summary["requested_runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
