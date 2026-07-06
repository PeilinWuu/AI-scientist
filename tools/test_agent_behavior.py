"""Evaluate dialogue intent and tool-use policy behavior."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.intent_router import IntentRouter
from src.agents.dialogue_orchestrator import DialogueOrchestrator
from src.config import settings
from src.llm.base import LLMProvider
from src.policies.tool_use_policy import decide_tool_permission
from src.state.conversation_state import ConversationState
from src.utils.readable_response import ensure_readable_assistant_message


class EvalRouterProvider(LLMProvider):
    """Small deterministic provider; IntentRouter still exercises Qwen-style JSON path."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        return json.dumps(
            {"intent": "research_consultation", "confidence": 0.3, "reason": "eval default"},
            ensure_ascii=False,
        )

    def metadata(self) -> dict:
        return {
            "provider": "qwen",
            "transport": "eval-double",
            "model": "intent-eval",
            "base_url": "",
            "is_mock": False,
        }


class EvalDialogueProvider(LLMProvider):
    """Deterministic dialogue provider for response-routing regression checks."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        if "IntentRouter" in system_prompt:
            return json.dumps(
                {"intent": "research_consultation", "confidence": 0.2, "reason": "eval default"},
                ensure_ascii=False,
            )
        if "response repair module" in system_prompt:
            return (
                "这是一个带流固耦合、运动控制和约束优化的低/中雷诺数推进问题。"
                "当前原型的 soft-swimmer lightweight tool 不能直接求解完整 Navier-Stokes/FSI/材料疲劳约束。"
                "下一步建议明确设计变量、目标函数、约束、边界条件，并准备 FreeFlow/CFD adapter。"
            )
        if "Current intent: capability_question" in system_prompt:
            return json.dumps(
                {
                    "assistant_message": "我的优势在于把科研目标澄清、实验规划、工具调用和结果解释串起来；当前工具是 soft-swimmer 轻量示例，边界是不能替代真实 CFD/FSI。",
                    "state_update": {},
                    "next_action": "ask_clarification",
                    "tool_call": {},
                },
                ensure_ascii=False,
            )
        if "Current intent: research_consultation" in system_prompt:
            return json.dumps(
                {
                    "assistant_message": "FlowScientist 是一个专注于流体模拟和流场优化的对话式 AI 科学家，它在以下几个方面具有优势。",
                    "state_update": {},
                    "next_action": "ask_clarification",
                    "tool_call": {},
                },
                ensure_ascii=False,
            )
        if "Current intent: tool_execution" in system_prompt:
            return json.dumps(
                {
                    "assistant_message": "我将运行当前内置的 soft-swimmer 示例工具。",
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
        return json.dumps(
            {
                "assistant_message": "你好。",
                "state_update": {},
                "next_action": "ask_clarification",
                "tool_call": {},
            },
            ensure_ascii=False,
        )

    def metadata(self) -> dict:
        return {
            "provider": "qwen",
            "transport": "eval-dialogue",
            "model": "dialogue-eval",
            "base_url": "",
            "is_mock": False,
        }


def main() -> int:
    cases = json.loads((PROJECT_ROOT / "evals" / "dialogue_behavior_cases.yaml").read_text(encoding="utf-8"))
    state = _new_state()
    router = IntentRouter(EvalRouterProvider(), Path(state.run_dir) / "llm_calls")
    rows = []
    failures = []

    for case in cases:
        if case["expected_tool"] == "generate_experiment_plot":
            _seed_experiment_history(state)
        expected_intents = case["expected_intent"]
        actual_intent = router.classify(case["user_message"], {"messages": state.messages})["intent"]
        proposed = _proposed_tool_for_case(case, actual_intent)
        permission = decide_tool_permission(actual_intent, case["user_message"], state, proposed)
        actual_tool = proposed.get("tool_name") if permission["allowed"] else None
        readable = ensure_readable_assistant_message(
            '{"candidate_id":"C001","amplitude":0.2,"frequency":1.1,"results":[]}'
        )
        shows_raw_json = readable.strip().startswith("{") or '"candidate_id"' in readable

        passed = (
            actual_intent in expected_intents
            and bool(actual_tool) == bool(case["should_call_tool"])
            and (case["expected_tool"] is None or actual_tool == case["expected_tool"])
            and shows_raw_json == bool(case["should_show_raw_json"])
        )
        row = {
            "case_id": case["case_id"],
            "expected_intent": "|".join(expected_intents),
            "actual_intent": actual_intent,
            "expected_tool": case["expected_tool"],
            "actual_tool": actual_tool,
            "pass": passed,
        }
        rows.append(row)
        if not passed:
            failures.append(row)
        if actual_intent in {"capability_question", "conceptual_explanation"} and actual_tool == "run_soft_swimmer_experiment":
            failures.append({**row, "error": "forbidden experiment tool call"})

    _print_rows(rows)
    if failures:
        print("\nAgent behavior eval failed:")
        _print_rows(failures)
        return 1
    dialogue_failures = _run_dialogue_regression()
    if dialogue_failures:
        print("\nDialogue response regression failed:")
        for item in dialogue_failures:
            print(item)
        return 1
    print("\nAgent behavior eval passed.")
    return 0


def _new_state() -> ConversationState:
    run_id = f"agent_behavior_eval_{datetime.now().strftime('%H%M%S')}_{uuid4().hex[:4]}"
    return ConversationState.create(
        run_id,
        settings.runs_dir / run_id,
        {
            "llm_provider": "qwen",
            "llm_transport": "eval-double",
            "llm_model": "intent-eval",
            "llm_base_url": "",
            "is_mock": False,
        },
    )


def _seed_experiment_history(state: ConversationState) -> None:
    if state.experiment_history:
        return
    state.experiment_history.append(
        {
            "tool_name": "run_soft_swimmer_experiment",
            "results": [
                {
                    "candidate_id": "C001",
                    "amplitude": 0.22,
                    "frequency": 1.1,
                    "wavelength": 1.0,
                    "stiffness": 0.55,
                    "phase": 0.2,
                    "mean_speed": 0.8,
                    "energy_cost": 0.5,
                    "efficiency": 1.4,
                    "stability_score": 0.9,
                    "vortex_loss": 0.1,
                    "constraint_violation": False,
                    "iteration": 1,
                }
            ],
            "best_candidate": {"candidate_id": "C001", "efficiency": 1.4},
            "summary": {"num_candidates": 1, "feasible_count": 1, "best_efficiency": 1.4},
        }
    )


def _proposed_tool_for_case(case: dict, actual_intent: str) -> dict:
    if actual_intent in {"capability_question", "conceptual_explanation", "research_consultation", "experiment_planning", "result_analysis"}:
        return {"tool_name": "run_soft_swimmer_experiment", "arguments": {"candidates": []}}
    if actual_intent == "visualization_request":
        return {"tool_name": "generate_experiment_plot", "arguments": {"figure_type": "efficiency_by_candidate"}}
    if actual_intent == "report_generation":
        return {"tool_name": "generate_research_plan_report", "arguments": {}}
    if actual_intent == "tool_execution":
        return {"tool_name": "run_soft_swimmer_experiment", "arguments": {"candidates": []}}
    return {}


def _print_rows(rows: list[dict]) -> None:
    print("case_id expected_intent actual_intent expected_tool actual_tool pass")
    for row in rows:
        print(
            f"{row['case_id']} {row['expected_intent']} {row['actual_intent']} "
            f"{row['expected_tool']} {row['actual_tool']} {row['pass']}"
        )


def _run_dialogue_regression() -> list[str]:
    failures: list[str] = []
    state = _new_dialogue_state()
    orchestrator = DialogueOrchestrator(state, llm=EvalDialogueProvider())

    state = orchestrator.handle_user_message("为什么说你在流场优化方面有优势？")
    reply = _last_assistant(state)
    if state.current_intent != "capability_question":
        failures.append(f"case1 intent={state.current_intent}")
    for text in ["优势", "工具", "边界"]:
        if text not in reply:
            failures.append(f"case1 missing response text: {text}")
    if state.total_tool_calls != 0:
        failures.append("case1 should not call tool")

    complex_goal = "仿生软体游泳机器人（微型海蛞蝓）过渡区流场与运动控制闭环优化，Re=150，包含目标函数和约束。"
    state = orchestrator.handle_user_message(complex_goal)
    reply = _last_assistant(state)
    if state.current_intent not in {"research_consultation", "experiment_planning"}:
        failures.append(f"case2 intent={state.current_intent}")
    for text in ["流固耦合", "约束优化", "下一步", "当前原型"]:
        if text not in reply:
            failures.append(f"case2 missing response text: {text}")
    forbidden = "FlowScientist 是一个专注于流体模拟和流场优化的对话式 AI 科学家，它在以下几个方面具有优势"
    if forbidden in reply:
        failures.append("case2 repeated capability template")
    if state.total_tool_calls != 0:
        failures.append("case2 should not call tool")

    state = orchestrator.handle_user_message("请直接运行当前内置的 soft-swimmer 示例工具，测试一组参数。")
    if state.current_intent != "tool_execution":
        failures.append(f"case3 intent={state.current_intent}")
    if state.total_tool_calls != 1:
        failures.append("case3 should call tool once")

    state = _new_dialogue_state()
    state = DialogueOrchestrator(state, llm=EvalDialogueProvider()).handle_user_message("你好")
    reply = _last_assistant(state)
    if state.current_intent != "casual_chat":
        failures.append(f"case4 intent={state.current_intent}")
    if len(reply) > 80 or "优势" in reply:
        failures.append("case4 greeting should be short")

    return failures


def _new_dialogue_state() -> ConversationState:
    run_id = f"agent_dialogue_eval_{datetime.now().strftime('%H%M%S')}_{uuid4().hex[:4]}"
    return ConversationState.create(
        run_id,
        settings.runs_dir / run_id,
        {
            "llm_provider": "qwen",
            "llm_transport": "eval-dialogue",
            "llm_model": "dialogue-eval",
            "llm_base_url": "",
            "is_mock": False,
        },
    )


def _last_assistant(state: ConversationState) -> str:
    for message in reversed(state.messages):
        if message.get("role") == "assistant":
            return str(message.get("content", ""))
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
