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
from src.policies.tool_use_policy import decide_tool_permission, resolve_user_controlled_search
from src.state.conversation_state import ConversationState
from src.utils.readable_response import ensure_readable_assistant_message


class EvalRouterProvider(LLMProvider):
    """Small deterministic provider; IntentRouter still exercises Qwen-style JSON path."""

    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
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

    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        if "IntentRouter" in system_prompt:
            return json.dumps(
                {"intent": "research_consultation", "confidence": 0.2, "reason": "eval default"},
                ensure_ascii=False,
            )
        if "Re=80" in user_prompt:
            return json.dumps(
                {
                    "assistant_message": "在 Re=80 这类低雷诺数微型游动问题里，只做完全对称的往复摆动通常会让形变路径前后抵消，净推进很弱。更合理的是设计非互易的形变循环，例如相位错开的摆动或行波式驱动，让一个周期内的身体形状变化不简单倒放。",
                    "state_update": {},
                    "next_action": "ask_clarification",
                    "tool_call": {},
                },
                ensure_ascii=False,
            )
        if "软体游动器的推进效率" in user_prompt:
            return json.dumps(
                {
                    "assistant_message": "第一轮建议扫波形级设计变量：振幅、频率、波长、相位和刚度。评价时不要只看推进效率，还要同时记录能耗、稳定性和约束是否满足；这样才能判断速度收益是否被能耗或不稳定摆动抵消。",
                    "state_update": {"planning_preference": "balanced_efficiency", "target_metric": "efficiency"},
                    "next_action": "propose_plan",
                    "tool_call": {},
                },
                ensure_ascii=False,
            )
        if "FreeFlow 输出 CSV" in user_prompt:
            return json.dumps(
                {
                    "assistant_message": "这类 FreeFlow 输出应先设计数据 schema，而不是转成 soft-swimmer demo。建议保留 candidate_id、设计变量、流场指标、目标指标、约束、可行性标记和仿真元数据，后续再统一转成分析表。",
                    "state_update": {},
                    "next_action": "ask_clarification",
                    "tool_call": {},
                },
                ensure_ascii=False,
            )
        if "论文依据" in user_prompt or "文献依据" in user_prompt:
            return json.dumps(
                {
                    "assistant_message": "可以参考低雷诺数游动的经典判断，例如 Purcell 关于低雷诺数生命的讨论，以及 Taylor 关于行波式游动片的早期模型。这里的核心结论是：低雷诺数下推进更依赖非互易形变和相位结构。",
                    "state_update": {},
                    "next_action": "ask_clarification",
                    "tool_call": {},
                },
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
    original_search_setting = settings.qwen_enable_search
    state = _new_state()
    router = IntentRouter(EvalRouterProvider(), Path(state.run_dir) / "llm_calls")
    rows = []
    failures = []

    for case in cases:
        settings.qwen_enable_search = bool(case.get("qwen_enable_search", True))
        if case["expected_tool"] == "generate_experiment_plot":
            _seed_experiment_history(state)
        expected_intents = case["expected_intent"]
        actual_intent = router.classify(case["user_message"], {"messages": state.messages})["intent"]
        proposed = _proposed_tool_for_case(case, actual_intent)
        permission = decide_tool_permission(actual_intent, case["user_message"], state, proposed)
        actual_tool = proposed.get("tool_name") if permission["allowed"] else None
        search_permission = resolve_user_controlled_search(
            case.get("web_search_mode", "off"),
            case["user_message"],
        )
        actual_search = bool(search_permission.get("enable_search"))
        actual_trigger = search_permission.get("search_trigger")
        expected_search = case.get("expected_search_used", case.get("expected_search"))
        readable = ensure_readable_assistant_message(
            '{"candidate_id":"C001","amplitude":0.2,"frequency":1.1,"results":[]}'
        )
        shows_raw_json = readable.strip().startswith("{") or '"candidate_id"' in readable

        passed = (
            actual_intent in expected_intents
            and bool(actual_tool) == bool(case["should_call_tool"])
            and (case["expected_tool"] is None or actual_tool == case["expected_tool"])
            and shows_raw_json == bool(case["should_show_raw_json"])
            and (expected_search is None or actual_search == bool(expected_search))
            and (case.get("expected_search_trigger") is None or actual_trigger == case["expected_search_trigger"])
        )
        row = {
            "case_id": case["case_id"],
            "expected_intent": "|".join(expected_intents),
            "actual_intent": actual_intent,
            "expected_tool": case["expected_tool"],
            "actual_tool": actual_tool,
            "expected_search": expected_search,
            "actual_search": actual_search,
            "expected_search_trigger": case.get("expected_search_trigger"),
            "actual_search_trigger": actual_trigger,
            "pass": passed,
        }
        rows.append(row)
        if not passed:
            failures.append(row)
        if actual_intent in {"capability_question", "conceptual_explanation"} and actual_tool == "run_soft_swimmer_experiment":
            failures.append({**row, "error": "forbidden experiment tool call"})

    _print_rows(rows)
    if failures:
        settings.qwen_enable_search = original_search_setting
        print("\nAgent behavior eval failed:")
        _print_rows(failures)
        return 1
    dialogue_failures = _run_dialogue_regression()
    if dialogue_failures:
        settings.qwen_enable_search = original_search_setting
        print("\nDialogue response regression failed:")
        for item in dialogue_failures:
            print(item)
        return 1
    print("\nAgent behavior eval passed.")
    settings.qwen_enable_search = original_search_setting
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
    print("case_id expected_intent actual_intent expected_tool actual_tool actual_search actual_search_trigger pass")
    for row in rows:
        print(
            f"{row['case_id']} {row['expected_intent']} {row['actual_intent']} "
            f"{row['expected_tool']} {row['actual_tool']} "
            f"{row.get('actual_search')} {row.get('actual_search_trigger')} {row['pass']}"
        )


def _run_dialogue_regression() -> list[str]:
    failures: list[str] = []
    forbidden_main_tokens = [
        "repair_note",
        "raw_response",
        "tool_name",
        "audit payload",
        "{",
    ]
    state = _new_dialogue_state()
    orchestrator = DialogueOrchestrator(state, llm=EvalDialogueProvider())

    state = orchestrator.handle_user_message("为什么说你在流场优化方面有优势？")
    reply = _last_assistant_user_facing_text(state)
    if state.current_intent != "capability_question":
        failures.append(f"case1 intent={state.current_intent}")
    for text in ["优势", "工具", "边界"]:
        if text not in reply:
            failures.append(f"case1 missing response text: {text}")
    if "问题理解" in reply and "建议下一步" in reply:
        failures.append("case1 reused research consultation template")
    failures.extend(_forbidden_token_failures("case1", reply, forbidden_main_tokens))
    if state.total_tool_calls != 0:
        failures.append("case1 should not call tool")

    complex_goal = "仿生软体游泳机器人（微型海蛞蝓）过渡区流场与运动控制闭环优化，Re=150，包含目标函数和约束。"
    state = orchestrator.handle_user_message(complex_goal)
    reply = _last_assistant_user_facing_text(state)
    if state.current_intent not in {"research_consultation", "experiment_planning"}:
        failures.append(f"case2 intent={state.current_intent}")
    for text in ["问题理解", "当前能力边界", "建议下一步", "流固耦合", "约束优化"]:
        if text not in reply:
            failures.append(f"case2 missing response text: {text}")
    forbidden = "FlowScientist 是一个专注于流体模拟和流场优化的对话式 AI 科学家，它在以下几个方面具有优势"
    if forbidden in reply:
        failures.append("case2 repeated capability template")
    failures.extend(_forbidden_token_failures("case2", reply, forbidden_main_tokens))
    if state.total_tool_calls != 0:
        failures.append("case2 should not call tool")

    state = orchestrator.handle_user_message("请直接运行当前内置的 soft-swimmer 示例工具，测试一组参数。")
    if state.current_intent != "tool_execution":
        failures.append(f"case3 intent={state.current_intent}")
    if state.total_tool_calls != 1:
        failures.append("case3 should call tool once")
    last_message = _last_assistant_message(state)
    if not last_message.get("tables"):
        failures.append("case3 should expose table-friendly rows")
    tool_reply = _last_assistant_user_facing_text(state)
    failures.extend(_forbidden_token_failures("case3", tool_reply, forbidden_main_tokens))

    state = _new_dialogue_state()
    state = DialogueOrchestrator(state, llm=EvalDialogueProvider()).handle_user_message("你好")
    reply = _last_assistant_user_facing_text(state)
    if state.current_intent != "casual_chat":
        failures.append(f"case4 intent={state.current_intent}")
    if len(reply) > 80 or "优势" in reply:
        failures.append("case4 greeting should be short")
    failures.extend(_forbidden_token_failures("case4", reply, forbidden_main_tokens))

    failures.extend(_run_domain_knowledge_regression(forbidden_main_tokens))
    failures.extend(_run_user_controlled_search_regression(forbidden_main_tokens))
    return failures


def _run_domain_knowledge_regression(forbidden_main_tokens: list[str]) -> list[str]:
    failures: list[str] = []
    cases = json.loads((PROJECT_ROOT / "evals" / "dialogue_behavior_cases.yaml").read_text(encoding="utf-8"))
    selected_cases = [case for case in cases if case.get("must_include") or case.get("may_include")]
    for case in selected_cases:
        state = _new_dialogue_state()
        orchestrator = DialogueOrchestrator(state, llm=EvalDialogueProvider())
        state = orchestrator.handle_user_message(case["user_message"])
        reply = _last_assistant_user_facing_text(state)
        expected_intents = case["expected_intent"]
        if state.current_intent not in expected_intents:
            failures.append(f"{case['case_id']} intent={state.current_intent}")
        if bool(state.total_tool_calls) != bool(case.get("should_call_tool")):
            failures.append(f"{case['case_id']} wrong tool call count: {state.total_tool_calls}")
        for text in case.get("must_include", []):
            if text not in reply:
                failures.append(f"{case['case_id']} missing required text: {text}")
        for text in case.get("must_not_include", []):
            if text in reply:
                failures.append(f"{case['case_id']} leaked forbidden text: {text}")
        failures.extend(_forbidden_token_failures(case["case_id"], reply, forbidden_main_tokens))
        if case.get("must_include"):
            selected = state.last_decision_trace.get("selected_principles", [])
            if not selected:
                failures.append(f"{case['case_id']} did not audit selected principles")
            audit_text = json.dumps(selected, ensure_ascii=False)
            if "source_ids" not in audit_text:
                failures.append(f"{case['case_id']} missing source_ids in decision trace")
            if any(source in reply for source in ["source_ids", "principle_id"]):
                failures.append(f"{case['case_id']} leaked principle audit data")
    return failures


def _run_user_controlled_search_regression(forbidden_main_tokens: list[str]) -> list[str]:
    failures: list[str] = []
    cases = json.loads((PROJECT_ROOT / "evals" / "dialogue_behavior_cases.yaml").read_text(encoding="utf-8"))
    selected_cases = [
        case
        for case in cases
        if case["case_id"].startswith("user_controlled_search_")
        or case["case_id"] in {"qwen_search_latest_papers", "qwen_search_freeflow_docs", "qwen_search_disabled_by_user"}
    ]
    original_search_setting = settings.qwen_enable_search
    try:
        for case in selected_cases:
            settings.qwen_enable_search = bool(case.get("qwen_enable_search", True))
            state = _new_dialogue_state()
            state.web_search_mode = case.get("web_search_mode", "off")
            initial_mode = state.web_search_mode
            orchestrator = DialogueOrchestrator(state, llm=EvalDialogueProvider())
            state = orchestrator.handle_user_message(case["user_message"])
            reply = _last_assistant_user_facing_text(state)

            expected_search = case.get("expected_search_used", case.get("expected_search"))
            if expected_search is not None and state.last_qwen_search_used != bool(expected_search):
                failures.append(
                    f"{case['case_id']} search_used={state.last_qwen_search_used}, expected={expected_search}"
                )
            expected_trigger = case.get("expected_search_trigger")
            if expected_trigger and state.last_search_trigger != expected_trigger:
                failures.append(
                    f"{case['case_id']} search_trigger={state.last_search_trigger}, expected={expected_trigger}"
                )
            for text in case.get("expected_contains", []):
                if text not in reply:
                    failures.append(f"{case['case_id']} missing expected text: {text}")
            for text in case.get("forbidden_contains", []):
                if text in reply:
                    failures.append(f"{case['case_id']} leaked forbidden text: {text}")
            failures.extend(_forbidden_token_failures(case["case_id"], reply, forbidden_main_tokens))
            if initial_mode == "this_turn" and state.web_search_mode != "off":
                failures.append(f"{case['case_id']} did not reset this_turn mode")
            if state.total_tool_calls:
                failures.append(f"{case['case_id']} should not call simulator tool")
    finally:
        settings.qwen_enable_search = original_search_setting
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


def _last_assistant_message(state: ConversationState) -> dict:
    for message in reversed(state.messages):
        if message.get("role") == "assistant":
            return message
    return {}


def _last_assistant_user_facing_text(state: ConversationState) -> str:
    message = _last_assistant_message(state)
    parts = [str(message.get("content", ""))]
    for section in message.get("sections", []) or []:
        parts.append(str(section.get("title", "")))
        parts.append(str(section.get("content", "")))
    for table in message.get("tables", []) or []:
        title = table.get("title")
        if title:
            parts.append(str(title))
    for figure in message.get("figures", []) or []:
        caption = figure.get("caption")
        if caption:
            parts.append(str(caption))
    for action in message.get("suggested_actions", []) or []:
        label = action.get("label")
        if label:
            parts.append(str(label))
    return "\n".join(part for part in parts if part)


def _forbidden_token_failures(case_id: str, reply: str, forbidden_tokens: list[str]) -> list[str]:
    failures: list[str] = []
    for token in forbidden_tokens:
        if token in reply:
            failures.append(f"{case_id} leaked forbidden token: {token}")
    if reply.count("candidate_id") > 1:
        failures.append(f"{case_id} repeated raw candidate_id")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
