"""Code-level tool-use approval policy."""

from __future__ import annotations

from typing import Any

from src.agents.intent_router import has_explicit_execution_request


BLOCKED_EXPERIMENT_INTENTS = {
    "casual_chat",
    "capability_question",
    "conceptual_explanation",
    "research_consultation",
}


def decide_tool_permission(
    intent: str,
    user_message: str,
    state: Any,
    proposed_tool_call: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a decision for whether a proposed tool call may execute."""

    tool_call = proposed_tool_call or {}
    tool_name = tool_call.get("tool_name")

    if not tool_name:
        return {
            "allowed": False,
            "requires_confirmation": False,
            "reason": "No tool call was proposed.",
            "user_facing_message": "",
        }

    if tool_name == "run_soft_swimmer_experiment":
        return _experiment_permission(intent, user_message)
    if tool_name == "generate_experiment_plot":
        return _plot_permission(intent, state)
    if tool_name == "generate_research_plan_report":
        return _report_permission(intent, state)

    return {
        "allowed": False,
        "requires_confirmation": False,
        "reason": f"Unknown or unsupported tool: {tool_name}.",
        "user_facing_message": "这个工具当前没有注册到可执行工具策略中，因此不会调用。",
    }


def _experiment_permission(intent: str, user_message: str) -> dict[str, Any]:
    if intent in BLOCKED_EXPERIMENT_INTENTS:
        return {
            "allowed": False,
            "requires_confirmation": False,
            "reason": f"Experiment tools are forbidden for intent={intent}.",
            "user_facing_message": _blocked_experiment_message(intent),
        }
    if intent == "experiment_planning":
        return {
            "allowed": False,
            "requires_confirmation": True,
            "reason": "Planning intent must ask for confirmation before execution.",
            "user_facing_message": (
                "我可以基于这个计划运行当前内置的 soft-swimmer 示例工具，但它只是轻量虚拟实验，"
                "不等价于真实 CFD/FSI。是否继续运行这个简化演示？"
            ),
        }
    if intent == "result_analysis" and "继续" not in user_message and "next round" not in user_message.lower():
        return {
            "allowed": False,
            "requires_confirmation": True,
            "reason": "Result analysis should not launch a new experiment without explicit next-round request.",
            "user_facing_message": "我会先分析已有结果；如果你希望继续下一轮，请明确说“继续下一轮实验”。",
        }
    if intent == "tool_execution":
        explicit = has_explicit_execution_request(user_message)
        return {
            "allowed": explicit,
            "requires_confirmation": not explicit,
            "reason": "Explicit execution request detected." if explicit else "Execution intent lacks explicit run/simulate/test wording.",
            "user_facing_message": "" if explicit else "请明确确认是否运行当前内置的 soft-swimmer 轻量示例工具。",
        }
    return {
        "allowed": False,
        "requires_confirmation": True,
        "reason": f"Intent {intent} is not allowed to run experiments by default.",
        "user_facing_message": "当前轮次不会自动运行实验；如需执行，请明确授权运行仿真或测试参数。",
    }


def _plot_permission(intent: str, state: Any) -> dict[str, Any]:
    has_data = bool(getattr(state, "experiment_history", []))
    if intent != "visualization_request":
        return {
            "allowed": False,
            "requires_confirmation": False,
            "reason": "Plot tool is only allowed for visualization_request intent.",
            "user_facing_message": "只有在你明确要求画图或可视化时，我才会调用图表工具。",
        }
    return {
        "allowed": has_data,
        "requires_confirmation": not has_data,
        "reason": "Experiment data is available." if has_data else "No experiment data is available for plotting.",
        "user_facing_message": "" if has_data else "我可以画图，但当前还没有实验结果。请先提供数据或运行一次实验。",
    }


def _report_permission(intent: str, state: Any) -> dict[str, Any]:
    has_context = bool(getattr(state, "messages", []) or getattr(state, "experiment_history", []))
    return {
        "allowed": intent == "report_generation" and has_context,
        "requires_confirmation": not has_context,
        "reason": "Report generation allowed." if has_context else "Not enough conversation or experiment history.",
        "user_facing_message": "" if has_context else "当前信息还不足以生成报告，请先补充研究目标或实验历史。",
    }


def _blocked_experiment_message(intent: str) -> str:
    if intent == "capability_question":
        return (
            "这个问题是在询问系统能力/优势，不应运行实验。我会先解释能力边界："
            "FlowScientist 的价值在于把 Qwen 的语言理解、实验规划、工具调用和结果解释组合起来，"
            "但当前可执行工具只是 soft-swimmer 轻量示例，不是完整 CFD/FSI 求解器。"
        )
    if intent == "conceptual_explanation":
        return "这是概念或原理解释问题，我会先解释，不会调用实验工具。"
    if intent == "research_consultation":
        return (
            "这是研究咨询问题，我会先分析问题结构、真实 CFD/FreeFlow 需求和下一步任务，"
            "不会直接运行当前的 toy simulator。"
        )
    return "这类对话不满足实验工具调用条件，因此不会运行工具。"
