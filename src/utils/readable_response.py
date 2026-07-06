"""Post-processing guards for user-facing dialogue messages."""

from __future__ import annotations

import json
from typing import Any


RAW_MARKERS = [
    '"candidate_id"',
    '"amplitude"',
    '"frequency"',
    '"results"',
    '"best_candidate"',
    "constraint_violation",
    "vortex_loss",
]


def ensure_readable_assistant_message(text: Any, context: dict[str, Any] | None = None) -> str:
    """Convert raw JSON-looking content into a readable chat message."""

    context = context or {}
    if isinstance(text, (dict, list)):
        return _summarize_raw_payload(text, context)
    text = "" if text is None else str(text)
    stripped = text.strip()
    if not stripped:
        return ""

    parsed = _try_parse_json(stripped) if _looks_like_json(stripped) else None
    if isinstance(parsed, dict):
        if parsed.get("assistant_message"):
            return ensure_readable_assistant_message(parsed["assistant_message"], context)
        return _summarize_raw_payload(parsed, context)
    if isinstance(parsed, list):
        return _summarize_raw_payload(parsed, context)

    if _looks_like_raw_tool_dump(stripped):
        return _summarize_raw_payload(stripped, context)
    if len(stripped) > 1800:
        return stripped[:1200].rstrip() + "\n\n（较长内容已压缩，原始数据保存在审计日志或折叠区。）"
    return stripped


def ensure_user_readable_response(decision: dict[str, Any]) -> dict[str, Any]:
    """Ensure assistant_message is natural language, not raw machine JSON."""

    normalized = dict(decision or {})
    message = normalized.get("assistant_message") or ""
    raw_data = normalized.get("raw_data")

    if isinstance(message, (dict, list)):
        raw_data = raw_data or message
    elif isinstance(message, str) and (_looks_like_json(message) or _looks_like_raw_tool_dump(message)):
        raw_data = raw_data or _try_parse_json(message) or message

    normalized["assistant_message"] = ensure_readable_assistant_message(message)
    if not normalized["assistant_message"]:
        normalized["assistant_message"] = "我需要更多信息才能继续。"
    if raw_data is not None:
        normalized["raw_data"] = raw_data
    return normalized


def summarize_tool_result(tool_name: str, result: dict[str, Any]) -> str:
    """Create a concise human-readable tool result summary."""

    if tool_name == "run_soft_swimmer_experiment":
        summary = result.get("summary", {})
        best = result.get("best_candidate", {}) or {}
        candidate_id = best.get("candidate_id", "best candidate")
        efficiency = _fmt(best.get("efficiency"))
        speed = _fmt(best.get("mean_speed"))
        energy = _fmt(best.get("energy_cost"))
        stability = _fmt(best.get("stability_score"))
        num = summary.get("num_candidates", len(result.get("results", [])))
        feasible = summary.get("feasible_count")
        return (
            f"Tool called: {tool_name}. 我测试了 {num} 组候选参数，"
            f"当前最佳候选是 {candidate_id}：efficiency={efficiency}，"
            f"mean_speed={speed}，energy_cost={energy}，stability_score={stability}。"
            f"可行候选数量为 {feasible}。原始结果已保存到工具日志和折叠区。"
        )
    if tool_name == "generate_experiment_plot":
        caption = result.get("caption") or result.get("summary") or "图表已生成。"
        return f"Tool called: {tool_name}. {caption}"
    if tool_name == "generate_research_plan_report":
        return "Tool called: generate_research_plan_report. 阶段报告已更新。"
    return f"Tool called: {tool_name}. 工具结果已保存，主聊天中只显示摘要。"


def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    return (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    )


def _try_parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None


def _looks_like_raw_tool_dump(text: str) -> bool:
    if not isinstance(text, str):
        return False
    marker_hits = sum(1 for marker in RAW_MARKERS if marker in text)
    brace_count = text.count("{") + text.count("}")
    return marker_hits >= 2 or brace_count > 12


def _summarize_raw_payload(payload: Any, context: dict[str, Any] | None = None) -> str:
    context = context or {}
    if isinstance(payload, dict):
        best = payload.get("best_candidate") or {}
        if best:
            return (
                f"我已处理结构化结果。当前最佳候选是 {best.get('candidate_id', 'unknown')}，"
                f"效率约为 {_fmt(best.get('efficiency'))}，约束状态请查看折叠区。"
            )
        if payload.get("tool_name"):
            return f"工具 {payload['tool_name']} 已返回结果，原始数据已放入折叠区。"
        return "我已收到结构化数据，并会把它转换为自然语言结论。"
    if isinstance(payload, list):
        return f"我已收到 {len(payload)} 条结构化记录，并会提炼关键结论。"
    return (
        "我已处理工具或模型返回的结构化内容。主聊天只显示摘要，"
        "原始 JSON 已保存在审计日志或折叠区。"
    )


def _fmt(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "n/a"
