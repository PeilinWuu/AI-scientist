"""Intent router and tool-policy guard smoke tests."""

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


class GuardTestProvider(LLMProvider):
    """Provider that deliberately over-calls tools so code guards are tested."""

    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        if "classified_intent=visualization_request" in user_prompt:
            return json.dumps(
                {
                    "assistant_message": "我将生成效率柱状图。",
                    "state_update": {},
                    "next_action": "call_tool",
                    "tool_call": {
                        "tool_name": "generate_experiment_plot",
                        "arguments": {"figure_type": "efficiency_by_candidate"},
                    },
                },
                ensure_ascii=False,
            )
        if "classified_intent=tool_execution" in user_prompt:
            return json.dumps(
                {
                    "assistant_message": "我将运行一次示例实验。",
                    "state_update": {"planning_preference": "balanced_efficiency"},
                    "next_action": "call_tool",
                    "tool_call": {
                        "tool_name": "run_soft_swimmer_experiment",
                        "arguments": {
                            "candidates": [
                                {"candidate_id": "C001", "amplitude": 0.22, "frequency": 1.1, "wavelength": 1.0, "stiffness": 0.55, "phase": 0.2},
                                {"candidate_id": "C002", "amplitude": 0.26, "frequency": 1.4, "wavelength": 1.2, "stiffness": 0.60, "phase": 0.35},
                                {"candidate_id": "C003", "amplitude": 0.30, "frequency": 1.7, "wavelength": 1.4, "stiffness": 0.50, "phase": 0.5},
                                {"candidate_id": "C004", "amplitude": 0.34, "frequency": 2.0, "wavelength": 1.6, "stiffness": 0.65, "phase": 0.65},
                            ],
                            "random_seed": 42,
                        },
                    },
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "assistant_message": "这是自然语言回复，不应该触发实验工具。",
                "state_update": {},
                "next_action": "call_tool",
                "tool_call": {
                    "tool_name": "run_soft_swimmer_experiment",
                    "arguments": {"candidates": []},
                },
            },
            ensure_ascii=False,
        )

    def metadata(self) -> dict:
        return {
            "provider": "qwen",
            "transport": "test-double",
            "model": "guard-test",
            "base_url": "",
            "is_mock": False,
        }


def main() -> int:
    state = _new_state()
    orchestrator = DialogueOrchestrator(state, llm=GuardTestProvider())

    cases = [
        ("你好，你能做什么？", {"capability_question", "casual_chat"}, 0),
        ("为什么说你在软体机器人鱼流场优化方面有优势？", {"capability_question", "conceptual_explanation"}, 0),
        ("我想优化机器鱼推进效率，请先帮我设计实验方案。", {"experiment_planning"}, 0),
    ]
    for text, expected_intents, expected_tool_calls in cases:
        state = orchestrator.handle_user_message(text)
        assert state.current_intent in expected_intents, (text, state.current_intent)
        assert state.total_tool_calls == expected_tool_calls, (text, state.total_tool_calls)

    state = orchestrator.handle_user_message("请运行一次实验，测试你建议的参数。")
    assert state.current_intent == "tool_execution"
    assert state.total_tool_calls == 1
    assert state.tool_calls[-1]["tool_name"] == "run_soft_swimmer_experiment"

    state = orchestrator.handle_user_message("把刚才的实验结果画成效率柱状图。")
    assert state.current_intent == "visualization_request"
    assert state.total_tool_calls == 2
    assert state.tool_calls[-1]["tool_name"] == "generate_experiment_plot"
    figure_path = Path(state.tool_calls[-1]["result"]["figure_path"])
    assert figure_path.exists(), figure_path

    print("Intent router test passed.")
    print(f"run_id={state.run_id}")
    print(f"tool_calls={state.total_tool_calls}")
    print(f"last_figure={figure_path}")
    return 0


def _new_state() -> ConversationState:
    run_id = f"intent_router_{datetime.now().strftime('%H%M%S')}_{uuid4().hex[:4]}"
    backend = {
        "llm_provider": "qwen",
        "llm_transport": "test-double",
        "llm_model": "guard-test",
        "llm_base_url": "",
        "is_mock": False,
    }
    return ConversationState.create(run_id, settings.runs_dir / run_id, backend)


if __name__ == "__main__":
    raise SystemExit(main())
