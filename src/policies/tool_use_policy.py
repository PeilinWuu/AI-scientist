"""Code-level tool-use approval policy."""

from __future__ import annotations

from typing import Any

from src.agents.intent_router import has_explicit_execution_request, has_explicit_web_search_request
from src.config import settings


BLOCKED_EXPERIMENT_INTENTS = {
    "casual_chat",
    "capability_question",
    "conceptual_explanation",
    "research_consultation",
    "web_research",
    "literature_search",
    "documentation_lookup",
    "current_info_lookup",
}

SEARCH_INTENTS = {
    "web_research",
    "literature_search",
    "documentation_lookup",
    "current_info_lookup",
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


def decide_qwen_search_permission(intent: str, user_message: str) -> dict[str, Any]:
    """Decide whether Qwen native web search may be enabled for this LLM call."""

    explicit = has_explicit_web_search_request(user_message)
    if _forbids_web_search(user_message):
        return _search_decision(False, "user_disabled_web_search", "User explicitly disabled web search.")
    if not settings.qwen_enable_search:
        return _search_decision(False, "qwen_search_disabled", "QWEN_ENABLE_SEARCH is false.")
    if intent not in SEARCH_INTENTS and not explicit:
        return _search_decision(False, "not_a_search_intent", f"Intent {intent} does not require web search.")
    if intent in {"casual_chat", "capability_question", "tool_execution"}:
        return _search_decision(False, "blocked_intent", f"Qwen search is blocked for intent={intent}.")
    if not explicit:
        return _search_decision(False, "no_explicit_search_request", "No explicit web/latest/literature/docs request.")

    trigger = _search_trigger(intent, user_message)
    options = _search_options()
    return {
        "allowed": True,
        "enable_search": True,
        "search_options": options,
        "search_trigger": trigger,
        "reason": "User explicitly requested public external information and QWEN_ENABLE_SEARCH=true.",
    }


def resolve_user_controlled_search(web_search_mode: str, user_message: str) -> dict[str, Any]:
    """Resolve Qwen native search from explicit UI state, with text opt-out priority."""

    mode = web_search_mode if web_search_mode in {"off", "this_turn", "always_on"} else "off"
    if _forbids_web_search(user_message):
        return _search_decision(
            False,
            "user_text_disabled_search",
            "User text explicitly disabled web search.",
        )
    if not settings.qwen_enable_search:
        return _search_decision(False, "qwen_search_disabled", "QWEN_ENABLE_SEARCH is false.")
    if mode == "this_turn":
        return {
            "allowed": True,
            "enable_search": True,
            "search_options": _search_options(force=True),
            "search_trigger": "user_selected_this_turn",
            "reason": "User selected web search for this turn in the UI.",
        }
    if mode == "always_on":
        return {
            "allowed": True,
            "enable_search": True,
            "search_options": _search_options(force=True),
            "search_trigger": "user_selected_always_on",
            "reason": "User selected always-on web search in the UI.",
        }
    return _search_decision(False, "user_search_off", "User search mode is off.")


def _search_decision(allowed: bool, trigger: str, reason: str) -> dict[str, Any]:
    return {
        "allowed": allowed,
        "enable_search": allowed,
        "search_options": {},
        "search_trigger": trigger,
        "reason": reason,
    }


def _search_options(force: bool = False) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if settings.qwen_search_strategy:
        options["search_strategy"] = settings.qwen_search_strategy
    if force or settings.qwen_search_force:
        options["forced_search"] = True
    if settings.qwen_search_freshness is not None:
        options["freshness"] = settings.qwen_search_freshness
    if settings.qwen_search_assigned_sites:
        options["assigned_site_list"] = settings.qwen_search_assigned_sites
    return options


def _search_trigger(intent: str, user_message: str) -> str:
    text = (user_message or "").lower()
    if intent == "literature_search" or any(item in text for item in ["paper", "literature", "citation", "references"]):
        return "user_requested_latest_papers"
    if intent == "documentation_lookup" or "documentation" in text or "docs" in text:
        return "user_requested_official_docs"
    if intent == "current_info_lookup":
        return "user_requested_current_info"
    return "user_requested_web_search"


def _forbids_web_search(user_message: str) -> bool:
    text = (user_message or "").lower()
    compact = text.replace(" ", "")
    chinese_blocks = [
        "不要联网",
        "别联网",
        "不用联网",
        "不要上网",
        "不用上网",
        "只根据已有知识",
        "只根据当前项目",
        "只看本地文件",
        "不要搜索",
        "不用搜索",
    ]
    english_blocks = [
        "do not search",
        "don't search",
        "no web",
        "without web",
        "do not browse",
        "don't browse",
        "only existing knowledge",
        "local files only",
    ]
    return any(item in compact for item in chinese_blocks) or any(item in text for item in english_blocks)


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
