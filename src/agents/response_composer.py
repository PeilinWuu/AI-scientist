"""User-facing response composer for FlowScientist."""

from __future__ import annotations

from typing import Any

from src.config import settings
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
    search_control = _search_control_metadata(skill_output)
    search_metadata = search_control if search_control.get("enable_search") else {}
    if search_control:
        raw_debug["qwen_search"] = search_control
    assistant_message = _extract_message(skill_output)
    references_allowed = _user_requested_references(user_message)

    if tool_result:
        return _filter_user_facing_response(
            _with_search_notice(_compose_tool_response(intent, selected_skill, tool_result, raw_debug), search_metadata),
            references_allowed=references_allowed,
        )

    if intent == "capability_question":
        return _filter_user_facing_response(
            _with_search_notice(
                _capability_response(assistant_message, raw_debug, state=state, user_message=user_message),
                search_metadata,
            ),
            references_allowed=references_allowed,
        )
    if intent == "research_consultation":
        return _filter_user_facing_response(
            _with_search_notice(
                _research_consultation_response(user_message, assistant_message, raw_debug),
                search_metadata,
            ),
            references_allowed=references_allowed,
        )
    if intent == "experiment_planning":
        return _filter_user_facing_response(
            _with_search_notice(_experiment_planning_response(assistant_message, tool_permission, raw_debug), search_metadata),
            references_allowed=references_allowed,
        )
    if intent == "casual_chat":
        return _filter_user_facing_response(
            _with_search_notice(_casual_response(assistant_message, raw_debug), search_metadata),
            references_allowed=references_allowed,
        )
    if intent in {"web_research", "literature_search", "documentation_lookup", "current_info_lookup"} and not search_metadata:
        return _filter_user_facing_response(
            _search_not_enabled_response(raw_debug, search_control),
            references_allowed=references_allowed,
        )

    return _filter_user_facing_response(_with_search_notice({
        "assistant_message": assistant_message or "我理解了。请继续补充你的目标、约束或希望我执行的下一步。",
        "sections": [],
        "tables": [],
        "figures": [],
        "suggested_actions": _default_actions(intent, state),
        "raw_debug": raw_debug,
    }, search_metadata), references_allowed=references_allowed)


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


def _capability_response(
    message: str,
    raw_debug: dict[str, Any],
    state: Any | None = None,
    user_message: str = "",
) -> dict[str, Any]:
    if _asks_web_capability(user_message):
        mode = getattr(state, "web_search_mode", "off") or "off"
        if settings.qwen_enable_search:
            mode_text = {
                "off": "当前系统支持联网搜索，但本轮未启用。你可以在界面中选择“仅本轮联网”或“始终联网”。",
                "this_turn": "当前系统支持联网搜索，你已选择“仅本轮联网”，本轮 Qwen 请求会启用联网搜索，处理完成后会自动恢复为“不联网”。",
                "always_on": "当前系统支持联网搜索，你已选择“始终联网”，后续每轮都会启用联网搜索，直到你在界面中关闭。",
            }.get(mode, "当前系统支持联网搜索，但是否启用由用户控制。")
            text = (
                f"{mode_text} 如果选择“不联网”，我会基于已有知识和当前项目上下文回答；"
                "如果选择“仅本轮联网”或“始终联网”，我会在对应 Qwen 请求中传入 enable_search。"
            )
        else:
            text = (
                "当前项目没有启用联网搜索配置。如果需要启用，请在 .env 中设置 "
                "QWEN_ENABLE_SEARCH=true，并确保 QwenProvider/CurlQwenProvider 会传入 enable_search 参数。"
            )
        return {
            "assistant_message": text,
            "sections": [
                {"title": "联网搜索状态", "content": text},
            ],
            "tables": [],
            "figures": [],
            "suggested_actions": [],
            "raw_debug": raw_debug,
        }
    if _asks_web_capability(user_message):
        mode = getattr(state, "web_search_mode", "off") or "off"
        if settings.qwen_enable_search:
            mode_text = {
                "off": "当前系统支持联网搜索，但本轮未启用。你可以在界面中选择“仅本轮联网”或“始终联网”。",
                "this_turn": "当前系统支持联网搜索，且你已选择“仅本轮联网”。本轮 Qwen 请求会启用搜索，处理完成后会自动恢复为不联网。",
                "always_on": "当前系统支持联网搜索，且你已选择“始终联网”。后续每轮都会启用搜索，直到你在界面中关闭。",
            }.get(mode, "当前系统支持联网搜索，但是否启用由你在界面中控制。")
            text = (
                f"{mode_text} 如果选择“不联网”，我会基于已有知识和本地项目上下文回答；"
                "如果选择“仅本轮联网”或“始终联网”，我会在对应 Qwen 请求中传入 enable_search。"
            )
        else:
            text = (
                "当前项目没有启用联网搜索配置。如果需要启用，请在 .env 中设置 "
                "QWEN_ENABLE_SEARCH=true，并确保 QwenProvider/CurlQwenProvider 会传入 enable_search 参数。"
            )
    else:
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


def _search_control_metadata(skill_output: dict[str, Any] | str) -> dict[str, Any]:
    if not isinstance(skill_output, dict):
        return {}
    options = dict(skill_output.get("llm_call_options") or {})
    return {
        "enable_search": bool(options.get("enable_search")),
        "search_options": dict(options.get("search_options") or {}),
        "search_trigger": str(options.get("search_trigger") or ""),
        "reason": str(options.get("reason") or ""),
    }


def _with_search_notice(response: dict[str, Any], search_metadata: dict[str, Any]) -> dict[str, Any]:
    if not search_metadata.get("enable_search"):
        return response
    updated = dict(response)
    message = str(updated.get("assistant_message", "")).strip()
    prefix = "我已基于联网搜索结果整理如下。"
    updated["assistant_message"] = f"{prefix}\n\n{message}" if message else prefix
    sections = list(updated.get("sections", []) or [])
    sections.insert(
        0,
        {
            "title": "联网搜索说明",
            "content": (
                "当前通过 Qwen 原生搜索获得信息，但 OpenAI-compatible Chat Completions "
                "对搜索来源结构化返回支持有限。如需严格引用和审计，建议后续切换 DashScope "
                "协议或封装独立 qwen_web_search_tool。"
            ),
        },
    )
    updated["sections"] = sections
    updated["search_used"] = True
    return updated


def _search_not_enabled_response(raw_debug: dict[str, Any], search_control: dict[str, Any]) -> dict[str, Any]:
    trigger = search_control.get("search_trigger") or "user_search_off"
    if trigger == "qwen_search_disabled" or not settings.qwen_enable_search:
        message = (
            "当前项目没有启用联网搜索配置。如果需要查在线资料，请在 .env 中设置 "
            "QWEN_ENABLE_SEARCH=true，然后重启前端。"
        )
    elif trigger == "user_text_disabled_search":
        message = "你已在文字中明确要求不要联网，本轮会只根据已有知识和当前项目上下文回答。"
    else:
        message = (
            "当前系统支持联网搜索，但本轮未启用联网搜索。"
            "如果你希望我查最新论文、官方文档或在线资料，可以在界面中打开“仅本轮联网”或“始终联网”。"
        )
    return {
        "assistant_message": message,
        "sections": [{"title": "联网搜索状态", "content": message}],
        "tables": [],
        "figures": [],
        "suggested_actions": [],
        "raw_debug": raw_debug,
    }
    if trigger == "qwen_search_disabled" or not settings.qwen_enable_search:
        message = (
            "当前项目没有启用联网搜索配置。如果需要联网查资料，请在 .env 中设置 "
            "QWEN_ENABLE_SEARCH=true，并重启前端。"
        )
    elif trigger == "user_text_disabled_search":
        message = "你已在文字中明确要求不要联网，所以我会只基于已有知识和当前项目上下文回答。"
    else:
        message = (
            "当前系统支持联网搜索，但本轮未启用联网搜索。"
            "如果你希望我查最新论文、官方文档或在线资料，可以在界面中打开“仅本轮联网”或“始终联网”。"
        )
    return {
        "assistant_message": message,
        "sections": [
            {
                "title": "联网搜索状态",
                "content": f"Last Search Trigger: {trigger}",
            }
        ],
        "tables": [],
        "figures": [],
        "suggested_actions": [],
        "raw_debug": raw_debug,
    }


REFERENCE_REQUEST_MARKERS = ["论文", "文献", "参考", "references", "paper", "citation", "来源", "依据"]

SOURCE_STYLE_PHRASES = [
    "根据 Purcell",
    "Purcell 1977",
    "根据 Taylor",
    "Taylor 1951",
    "论文指出",
    "文献表明",
    "根据文献",
    "根据知识库",
    "检索结果显示",
    "according to the literature",
    "the paper states",
]


def _filter_user_facing_response(
    response: dict[str, Any],
    references_allowed: bool,
) -> dict[str, Any]:
    if references_allowed:
        return response
    filtered = dict(response)
    filtered["assistant_message"] = _suppress_source_style(str(filtered.get("assistant_message", "")))
    sections = []
    for section in filtered.get("sections", []) or []:
        item = dict(section)
        item["title"] = _suppress_source_style(str(item.get("title", "")))
        item["content"] = _suppress_source_style(str(item.get("content", "")))
        sections.append(item)
    filtered["sections"] = sections
    return filtered


def _suppress_source_style(text: str) -> str:
    output = text
    for phrase in SOURCE_STYLE_PHRASES:
        output = output.replace(phrase, "")
    if settings.qwen_enable_search:
        for phrase in [
            "我无法直接访问互联网",
            "我无法访问互联网",
            "我不能联网",
            "不能联网",
            "我无法直接访问外部数据源",
        ]:
            output = output.replace(phrase, "当前系统支持联网搜索，但是否启用由用户控制")
        for phrase in ["我无法直接访问互联网", "我无法访问互联网", "我不能联网", "不能联网", "我无法直接访问外部数据源"]:
            output = output.replace(phrase, "当前系统支持联网搜索，但是否启用由用户控制")
    return " ".join(output.split())


def _user_requested_references(user_message: str) -> bool:
    text = (user_message or "").lower()
    return any(marker in text for marker in REFERENCE_REQUEST_MARKERS)


def _asks_web_capability(user_message: str) -> bool:
    text = (user_message or "").lower().replace(" ", "")
    return any(marker in text for marker in ["能联网吗", "可以联网吗", "会联网吗", "能不能联网", "能上网吗", "可以上网吗"]) or any(
        marker in (user_message or "").lower()
        for marker in ["can you browse", "can you search the web", "internet access", "web search"]
    )


def _looks_complex_research_task(text: str) -> bool:
    markers = ["流固耦合", "Re", "目标函数", "约束", "Navier", "FSI", "疲劳", "闭环", "边界条件"]
    return sum(1 for marker in markers if marker.lower() in text.lower()) >= 2
