"""User-facing response composer for FlowScientist."""

from __future__ import annotations

from typing import Any

from src.skills.tool_result_summarizer import summarize_soft_swimmer_result
from src.utils.readable_response import ensure_readable_assistant_message


def compose_user_response(
    intent: str,
    selected_skill: str,
    user_message: str,
    skill_output: dict[str, Any] | str,
    tool_result: dict[str, Any] | None = None,
    tool_permission: dict[str, Any] | None = None,
    state: Any | None = None,
) -> dict[str, Any]:
    """Compose a user-facing response from skill/tool/audit data."""

    tool_permission = tool_permission or {}
    raw_debug = {
        "intent": intent,
        "selected_skill": selected_skill,
        "tool_permission": tool_permission,
    }
    if isinstance(skill_output, dict):
        raw_debug["skill_output_keys"] = sorted(skill_output.keys())
    assistant_message = _extract_message(skill_output)

    if tool_result:
        return _compose_tool_response(intent, selected_skill, tool_result, raw_debug)

    if intent == "capability_question":
        return _capability_response(assistant_message, raw_debug)
    if intent == "research_consultation":
        return _research_consultation_response(user_message, assistant_message, raw_debug)
    if intent == "experiment_planning":
        return _experiment_planning_response(assistant_message, tool_permission, raw_debug)
    if intent == "casual_chat":
        return _casual_response(assistant_message, raw_debug)

    return {
        "assistant_message": assistant_message or "我理解了。请继续补充你的目标、约束或希望我执行的下一步。",
        "sections": [],
        "tables": [],
        "figures": [],
        "suggested_actions": _default_actions(intent, state),
        "raw_debug": raw_debug,
    }


def _compose_tool_response(
    intent: str,
    selected_skill: str,
    tool_result: dict[str, Any],
    raw_debug: dict[str, Any],
) -> dict[str, Any]:
    tool_name = tool_result.get("tool_name")
    if tool_name == "run_soft_swimmer_experiment":
        summary = summarize_soft_swimmer_result(tool_result)
        return {
            "assistant_message": summary["summary"],
            "sections": summary["sections"],
            "tables": summary["tables"],
            "figures": [],
            "suggested_actions": summary["suggested_actions"],
            "raw_debug": {**raw_debug, "tool_name": tool_name},
        }
    if tool_name == "generate_experiment_plot":
        return {
            "assistant_message": tool_result.get("summary") or tool_result.get("caption") or "图表已生成。",
            "sections": [{"title": "图表说明", "content": tool_result.get("caption", "")}],
            "tables": [],
            "figures": [
                {
                    "figure_path": tool_result.get("figure_path"),
                    "caption": tool_result.get("caption", ""),
                    "figure_type": tool_result.get("figure_type", ""),
                }
            ],
            "suggested_actions": [],
            "raw_debug": {**raw_debug, "tool_name": tool_name},
        }
    return {
        "assistant_message": "工具已完成，结果已转换为用户可读摘要。",
        "sections": [],
        "tables": [],
        "figures": [],
        "suggested_actions": [],
        "raw_debug": {**raw_debug, "tool_name": tool_name},
    }


def _capability_response(message: str, raw_debug: dict[str, Any]) -> dict[str, Any]:
    text = message or (
        "我的优势在于把科研目标澄清、实验任务规划、工具调用、结果解释和下一轮迭代建议串起来。"
        "当前已实现的可执行工具是 lightweight soft-swimmer virtual experiment；它可以扩展到 "
        "FreeFlow、CFD solver、实验仪器 API 或后处理脚本，但不能假装已经具备完整 CFD/FSI 求解能力。"
    )
    return {
        "assistant_message": text,
        "sections": [
            {"title": "能力定位", "content": "通用流体仿真与流场优化 AI Scientist。"},
            {"title": "当前工具", "content": "已实现 lightweight soft-swimmer virtual experiment 和图表工具。"},
            {"title": "能力边界", "content": "当前原型不能直接求解完整 Navier-Stokes/FSI/材料疲劳问题。"},
        ],
        "tables": [],
        "figures": [],
        "suggested_actions": [
            {"label": "设计 CFD adapter 数据接口", "action": "design_adapter"},
            {"label": "生成实验任务规划", "action": "generate_plan"},
        ],
        "raw_debug": raw_debug,
    }


def _research_consultation_response(
    user_message: str, message: str, raw_debug: dict[str, Any]
) -> dict[str, Any]:
    if _looks_complex_research_task(user_message):
        assistant_message = (
            "这是一个带流固耦合、运动控制和约束优化的流体仿真研究任务。"
            "当前不应直接运行简化示例工具，而应先拆解问题并确定仿真接口。"
        )
        sections = [
            {
                "title": "问题理解",
                "content": (
                    "这是低/中雷诺数下的非定常推进与流固耦合优化问题。"
                    "设计变量可能包括运动学参数、材料刚度参数和控制律参数；约束包括推进力、"
                    "材料疲劳、能量耗散和稳定性。"
                ),
            },
            {
                "title": "当前能力边界",
                "content": (
                    "当前内置工具只是 lightweight soft-swimmer virtual experiment，"
                    "不能直接求解完整 Navier-Stokes/FSI/材料疲劳约束，但可作为低保真探索或演示工具。"
                ),
            },
            {
                "title": "建议下一步",
                "content": (
                    "先明确设计变量范围、目标函数和约束；再设计 CFD/FreeFlow adapter 输出 schema；"
                    "随后用低保真参数探索筛选区域，再接入高保真仿真验证。"
                ),
            },
        ]
    else:
        assistant_message = message or "这是一个流体仿真优化研究问题，我建议先拆解目标、变量、约束和数据接口。"
        sections = [
            {"title": "问题理解", "content": "我会先识别问题类型、关键变量、目标函数和约束。"},
            {"title": "当前能力边界", "content": "当前可执行实验工具是 soft-swimmer 轻量示例，不等价于完整 CFD。"},
            {"title": "建议下一步", "content": "可以先生成实验任务规划，或设计 CFD/FreeFlow adapter 数据接口。"},
        ]
    return {
        "assistant_message": assistant_message,
        "sections": sections,
        "tables": [],
        "figures": [],
        "suggested_actions": [
            {"label": "生成实验任务规划", "action": "generate_plan"},
            {"label": "设计 CFD adapter 数据接口", "action": "design_adapter"},
            {"label": "运行当前简化 soft-swimmer 示例工具", "action": "confirm_run_soft_swimmer"},
            {"label": "生成阶段报告", "action": "generate_report"},
        ],
        "raw_debug": raw_debug,
    }


def _experiment_planning_response(
    message: str, tool_permission: dict[str, Any], raw_debug: dict[str, Any]
) -> dict[str, Any]:
    return {
        "assistant_message": message
        or "我可以先给出实验任务规划；如果你确认，再运行当前简化 soft-swimmer 示例工具。",
        "sections": [
            {"title": "实验任务规划", "content": message or "建议先定义变量范围、目标函数、约束和评价指标。"},
            {"title": "执行确认", "content": tool_permission.get("user_facing_message") or "需要你明确确认后才会运行工具。"},
        ],
        "tables": [],
        "figures": [],
        "suggested_actions": [
            {"label": "运行当前简化 soft-swimmer 示例工具", "action": "confirm_run_soft_swimmer"},
            {"label": "设计 CFD adapter 数据接口", "action": "design_adapter"},
        ],
        "raw_debug": raw_debug,
    }


def _casual_response(message: str, raw_debug: dict[str, Any]) -> dict[str, Any]:
    short = message.strip() if message else "你好，我在。你可以告诉我你的流体仿真或优化问题。"
    if len(short) > 120:
        short = "你好，我在。你可以告诉我你的流体仿真目标、约束，或希望我规划的下一步。"
    return {
        "assistant_message": short,
        "sections": [],
        "tables": [],
        "figures": [],
        "suggested_actions": [],
        "raw_debug": raw_debug,
    }


def _default_actions(intent: str, state: Any | None) -> list[dict[str, str]]:
    actions = [
        {"label": "生成实验任务规划", "action": "generate_plan"},
        {"label": "设计 CFD adapter 数据接口", "action": "design_adapter"},
    ]
    if getattr(state, "experiment_history", None):
        actions.append({"label": "画图展示已有结果", "action": "plot_efficiency"})
    return actions


def _extract_message(skill_output: dict[str, Any] | str) -> str:
    if isinstance(skill_output, dict):
        text = skill_output.get("assistant_message") or ""
    else:
        text = skill_output
    if isinstance(text, str) and "repair_note" in text:
        return "我已经恢复了对话输出，将只展示用户可读的科研反馈。"
    return ensure_readable_assistant_message(text)


def _looks_complex_research_task(text: str) -> bool:
    markers = ["流固耦合", "Re", "目标函数", "约束", "Navier", "FSI", "疲劳", "闭环", "边界条件"]
    return sum(1 for marker in markers if marker.lower() in text.lower()) >= 2
