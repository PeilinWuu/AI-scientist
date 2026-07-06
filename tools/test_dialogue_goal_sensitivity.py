"""Goal sensitivity test for the chat-first DialogueOrchestrator."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.dialogue_orchestrator import DialogueOrchestrator
from src.config import settings
from src.llm import get_llm_status
from src.state.conversation_state import ConversationState


def main() -> int:
    status = get_llm_status()
    if status["mock_mode"] or status["llm_provider"] != "qwen":
        print("Dialogue sensitivity requires real Qwen.")
        return 1

    goals = [
        "I want to maximize swimming speed, energy cost can increase moderately.",
        "I want to minimize energy cost and keep swimming stable.",
    ]
    rows = []
    for goal in goals:
        state = _new_state(status)
        orchestrator = DialogueOrchestrator(state)
        state = orchestrator.handle_user_message(goal)
        rows.append(_summarize_state(state))

    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    different_preference = df.loc[0, "planning_preference"] != df.loc[1, "planning_preference"]
    param_delta = max(
        abs(float(df.loc[0, "avg_frequency"]) - float(df.loc[1, "avg_frequency"])),
        abs(float(df.loc[0, "avg_amplitude"]) - float(df.loc[1, "avg_amplitude"])),
        abs(float(df.loc[0, "avg_stiffness"]) - float(df.loc[1, "avg_stiffness"])),
    )
    assistant_diff = df.loc[0, "assistant_excerpt"] != df.loc[1, "assistant_excerpt"]
    if not different_preference or param_delta < 0.05 or not assistant_diff:
        print("Dialogue goal sensitivity failed.")
        return 1
    print("Dialogue goal sensitivity passed.")
    return 0


def _new_state(status: dict) -> ConversationState:
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    backend = {
        "llm_provider": status["llm_provider"],
        "llm_transport": status["llm_transport"],
        "llm_model": status["llm_model"],
        "llm_base_url": status["llm_base_url"],
        "is_mock": status["mock_mode"],
    }
    return ConversationState.create(run_id, settings.runs_dir / run_id, backend)


def _summarize_state(state: ConversationState) -> dict:
    if not state.tool_calls:
        raise RuntimeError(f"Run {state.run_id} did not call a tool.")
    request_files = sorted((Path(state.run_dir) / "tool_calls").glob("*_request.json"))
    request = json.loads(request_files[-1].read_text(encoding="utf-8"))
    candidates = request["arguments"].get("candidates", [])
    assistant_messages = [msg["content"] for msg in state.messages if msg["role"] == "assistant"]
    return {
        "run_id": state.run_id,
        "planning_preference": state.planning_preference,
        "target_metric": state.target_metric,
        "avg_frequency": round(sum(c["frequency"] for c in candidates) / len(candidates), 4),
        "avg_amplitude": round(sum(c["amplitude"] for c in candidates) / len(candidates), 4),
        "avg_stiffness": round(sum(c["stiffness"] for c in candidates) / len(candidates), 4),
        "llm_calls_count": state.total_llm_calls,
        "tool_calls_count": state.total_tool_calls,
        "real_qwen_run": (
            state.llm_backend.get("llm_provider") == "qwen"
            and state.llm_backend.get("is_mock") is False
        ),
        "assistant_excerpt": (assistant_messages[-1] if assistant_messages else "")[:120],
    }


if __name__ == "__main__":
    raise SystemExit(main())
