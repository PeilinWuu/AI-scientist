"""Smoke test for greeting dialogue and JSON repair robustness."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.dialogue_orchestrator import DialogueOrchestrator
from src.config import settings
from src.llm import get_llm_status
from src.state.conversation_state import ConversationState


def main() -> int:
    status = get_llm_status()
    run_id = f"json_repair_{datetime.now().strftime('%H%M%S')}_{uuid4().hex[:4]}"
    state = ConversationState.create(
        run_id,
        settings.runs_dir / run_id,
        {
            "llm_provider": status["llm_provider"],
            "llm_transport": status["llm_transport"],
            "llm_model": status["llm_model"],
            "llm_base_url": status["llm_base_url"],
            "is_mock": status["mock_mode"],
        },
    )
    state = DialogueOrchestrator(state).handle_user_message("你好，你能做什么？")
    assistant_messages = [
        message for message in state.messages if message["role"] == "assistant"
    ]
    if not assistant_messages:
        print("Dialogue JSON repair test failed: no assistant message.")
        return 1
    last = assistant_messages[-1]
    if last.get("next_action") not in {"ask_clarification", "propose_plan"}:
        print(f"Dialogue JSON repair test failed: next_action={last.get('next_action')}")
        return 1
    if state.total_tool_calls != 0:
        print("Dialogue JSON repair test failed: greeting should not call simulator tool.")
        return 1
    print("Dialogue JSON repair test passed.")
    print(f"run_id={state.run_id}")
    print(f"assistant_message={last['content'][:200]}")
    print(f"llm_calls={state.total_llm_calls}")
    print(f"tool_calls={state.total_tool_calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
