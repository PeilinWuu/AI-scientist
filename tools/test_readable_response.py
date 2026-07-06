"""Readable response guard smoke tests."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.dialogue_orchestrator import DialogueOrchestrator
from src.config import settings
from src.llm.base import LLMProvider
from src.state.conversation_state import ConversationState
from src.utils.readable_response import ensure_user_readable_response


class JsonDumpProvider(LLMProvider):
    """Provider that emits raw-looking JSON as assistant content."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        raw_message = json.dumps(
            {
                "results": [
                    {"candidate_id": "C001", "efficiency": 1.2},
                    {"candidate_id": "C002", "efficiency": 1.4},
                ],
                "best_candidate": {"candidate_id": "C002"},
            },
            ensure_ascii=False,
        )
        return json.dumps(
            {
                "assistant_message": raw_message,
                "state_update": {},
                "next_action": "call_tool",
                "tool_call": {
                    "tool_name": "run_soft_swimmer_experiment",
                    "arguments": {
                        "candidates": [
                            {"candidate_id": "C001", "amplitude": 0.22, "frequency": 1.1, "wavelength": 1.0, "stiffness": 0.55, "phase": 0.2},
                            {"candidate_id": "C002", "amplitude": 0.26, "frequency": 1.4, "wavelength": 1.2, "stiffness": 0.60, "phase": 0.35},
                            {"candidate_id": "C003", "amplitude": 0.30, "frequency": 1.7, "wavelength": 1.4, "stiffness": 0.50, "phase": 0.5},
                            {"candidate_id": "C004", "amplitude": 0.34, "frequency": 2.0, "wavelength": 1.6, "stiffness": 0.65, "phase": 0.65},
                        ]
                    },
                },
            },
            ensure_ascii=False,
        )

    def metadata(self) -> dict:
        return {
            "provider": "qwen",
            "transport": "test-double",
            "model": "readable-test",
            "base_url": "",
            "is_mock": False,
        }


def main() -> int:
    guarded = ensure_user_readable_response(
        {
            "assistant_message": '{"candidate_id": "C001", "results": [{"efficiency": 1.2}]}',
            "state_update": {},
            "next_action": "ask_clarification",
            "tool_call": {},
        }
    )
    assert not guarded["assistant_message"].strip().startswith("{")
    assert "candidate_id" not in guarded["assistant_message"]
    assert "raw_data" in guarded

    state = _new_state()
    state = DialogueOrchestrator(state, llm=JsonDumpProvider()).handle_user_message(
        "请运行一次实验，测试你建议的参数。"
    )
    visible_messages = [item["content"] for item in state.messages]
    assert all(not text.strip().startswith("{") for text in visible_messages)
    assert all("candidate_id" not in text for text in visible_messages if len(text) > 80)
    assert any(item.get("raw_data") for item in state.messages), "raw data should be collapsible"
    assert max(len(text) for text in visible_messages) < 1800

    print("Readable response test passed.")
    print(f"run_id={state.run_id}")
    print(f"messages={len(state.messages)}")
    print(f"tool_calls={state.total_tool_calls}")
    return 0


def _new_state() -> ConversationState:
    run_id = f"readable_response_{datetime.now().strftime('%H%M%S')}_{uuid4().hex[:4]}"
    backend = {
        "llm_provider": "qwen",
        "llm_transport": "test-double",
        "llm_model": "readable-test",
        "llm_base_url": "",
        "is_mock": False,
    }
    return ConversationState.create(run_id, settings.runs_dir / run_id, backend)


if __name__ == "__main__":
    raise SystemExit(main())
