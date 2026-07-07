"""Qwen-backed intent router with deterministic safety correction."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.llm import get_llm_provider
from src.llm.base import LLMProvider
from src.skills.intent_router_skill import INTENT_ROUTER_SKILL, INTENTS
from src.utils.io import ensure_dir, write_json
from src.utils.llm_audit import parse_llm_json


class IntentRouter:
    """Classify user intent and persist the Qwen routing evidence."""

    def __init__(self, llm: LLMProvider | None = None, llm_calls_dir: str | Path | None = None) -> None:
        self.llm = llm or get_llm_provider()
        self.llm_calls_dir = Path(llm_calls_dir) if llm_calls_dir else None
        if self.llm_calls_dir:
            ensure_dir(self.llm_calls_dir)

    def classify(self, user_message: str, state_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return intent metadata with Qwen output plus rule-based correction."""

        state_snapshot = state_snapshot or {}
        system_prompt = (
            "You are the IntentRouter for FlowScientist. Return strict JSON only.\n"
            f"{INTENT_ROUTER_SKILL}\n"
            "Schema: {\"intent\":\"...\", \"confidence\":0.0, \"reason\":\"...\"}."
        )
        user_prompt = (
            f"user_message={user_message}\n"
            f"state_snapshot={json.dumps(state_snapshot, ensure_ascii=False)}\n"
            f"valid_intents={json.dumps(INTENTS, ensure_ascii=False)}"
        )
        raw = self._record_call(system_prompt, user_prompt)
        qwen_intent = "research_consultation"
        confidence = 0.0
        reason = ""
        try:
            parsed = parse_llm_json(raw, "IntentRouter")
            qwen_intent = str(parsed.get("intent") or qwen_intent)
            confidence = float(parsed.get("confidence") or 0.0)
            reason = str(parsed.get("reason") or "")
        except Exception as exc:  # noqa: BLE001 - router should continue with safety rules.
            reason = f"Qwen intent parse failed; using rule correction: {type(exc).__name__}"

        if qwen_intent not in INTENTS:
            qwen_intent = "research_consultation"
        corrected = correct_intent_with_rules(user_message, qwen_intent)
        result = {
            "intent": corrected,
            "qwen_intent": qwen_intent,
            "confidence": confidence,
            "reason": reason,
            "rule_corrected": corrected != qwen_intent,
        }
        self._record_router_result(result)
        return result

    def _record_call(self, system_prompt: str, user_prompt: str) -> str:
        metadata = self.llm.metadata()
        raw = ""
        index = self._next_index()
        if self.llm_calls_dir:
            prompt = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}"
            write_json(
                self.llm_calls_dir / f"{index:03d}_intent_router_request.json",
                {
                    "agent": "IntentRouter",
                    "provider": metadata.get("provider"),
                    "transport": metadata.get("transport"),
                    "model": metadata.get("model"),
                    "is_mock": bool(metadata.get("is_mock", True)),
                    "prompt": prompt,
                    "prompt_sha256": _hash(prompt),
                    "timestamp": _timestamp(),
                },
            )
        raw = self.llm.generate(system_prompt, user_prompt) or ""
        if self.llm_calls_dir:
            write_json(
                self.llm_calls_dir / f"{index:03d}_intent_router_response.json",
                {
                    "agent": "IntentRouter",
                    "provider": metadata.get("provider"),
                    "transport": metadata.get("transport"),
                    "model": metadata.get("model"),
                    "is_mock": bool(metadata.get("is_mock", True)),
                    "raw_response": raw,
                    "response_sha256": _hash(raw),
                    "timestamp": _timestamp(),
                },
            )
        return raw

    def _record_router_result(self, result: dict[str, Any]) -> None:
        if not self.llm_calls_dir:
            return
        index = self._next_index("*_intent_router_result.json")
        write_json(
            self.llm_calls_dir / f"{index:03d}_intent_router_result.json",
            {**result, "timestamp": _timestamp()},
        )

    def _next_index(self, pattern: str = "*_request.json") -> int:
        if not self.llm_calls_dir:
            return 1
        return len(list(self.llm_calls_dir.glob(pattern))) + 1


def correct_intent_with_rules(user_message: str, qwen_intent: str) -> str:
    """Safety correction layer. Rules do not replace Qwen; they prevent unsafe routing."""

    text = (user_message or "").strip().lower()
    compact = text.replace(" ", "")

    if _has_any(
        compact,
        [
            "你能联网吗",
            "可以联网吗",
            "会联网吗",
            "能不能联网",
            "你能上网吗",
            "可以上网吗",
            "支持联网吗",
            "支持联网搜索吗",
        ],
    ) or _has_any(text, ["can you browse", "can you search the web", "internet access", "web search capability"]):
        return "capability_question"

    if _has_any(compact, ["画图", "可视化", "图表", "曲线", "散点", "柱状", "对比"]) or _has_any(
        text, ["show plot", "chart", "curve", "scatter", "visualize", "visualization", "compare"]
    ):
        return "visualization_request"
    if _forbids_web_search(user_message):
        if _has_any(compact, ["什么是", "为什么", "原理", "概念", "如何理解", "解释一下"]) or _has_any(
            text, ["what is", "why", "principle", "concept", "explain", "how to understand"]
        ):
            return "conceptual_explanation"
        return qwen_intent if qwen_intent in INTENTS else "research_consultation"
    search_intent = _search_intent(user_message)
    if search_intent:
        return search_intent
    if _has_any(compact, ["报告", "总结", "ppt", "研究计划", "技术路线"]) or _has_any(
        text, ["report", "summary", "ppt", "research plan", "technical route"]
    ):
        return "report_generation"
    if has_explicit_execution_request(user_message):
        return "tool_execution"
    if (
        ("freeflow" in compact and _has_any(compact, ["接入", "adapter", "适配", "数据接口"]))
        or _has_any(compact, ["cfdadapter", "数据接口", "接入系统", "适配器"])
    ):
        return "research_consultation"
    if _has_any(compact, ["设计实验", "设计一轮", "实验方案", "规划参数", "实验计划", "规划下一轮", "下一步仿真任务", "下一步实验"]) or _has_any(
        text, ["design an experiment", "experiment plan", "plan parameters", "next simulation task", "next experiment"]
    ):
        return "experiment_planning"
    if _has_any(compact, ["结果", "分析刚才", "解释结果", "已有数据"]) or _has_any(
        text, ["result", "analyze existing", "existing data"]
    ):
        return "result_analysis"
    if _has_any(compact, ["你能做什么", "能做什么", "有什么用", "优势", "项目定位", "项目意义", "能力边界"]) or _has_any(
        text, ["what can you do", "capability", "advantage", "why are you useful", "system boundary", "positioning"]
    ):
        return "capability_question"
    if _has_any(compact, ["什么是", "为什么", "原理", "概念", "如何理解", "解释一下"]) or _has_any(
        text, ["what is", "why", "principle", "concept", "explain", "how to understand"]
    ):
        return "conceptual_explanation"
    if _has_any(compact, ["你好", "谢谢", "感谢", "早上好", "晚上好"]) or text in {"hi", "hello", "hey", "thanks"}:
        return "casual_chat"
    if _looks_like_fluid_research(text, compact):
        return "research_consultation"
    return qwen_intent if qwen_intent in INTENTS else "research_consultation"


def has_explicit_execution_request(user_message: str) -> bool:
    """Detect explicit permission to run a tool/simulation."""

    text = (user_message or "").lower()
    compact = text.replace(" ", "")
    return _has_any(
        compact,
        [
            "运行实验",
            "运行一次实验",
            "直接运行",
            "运行当前",
            "运行你刚才建议",
            "运行刚才建议",
            "开始实验",
            "开始仿真",
            "调用工具",
            "测试这些参数",
            "测试你建议",
            "继续下一轮",
            "执行实验",
            "跑一下",
        ],
    ) or _has_any(
        text,
        [
            "run the experiment",
            "run an experiment",
            "run experiment",
            "run an initial experiment",
            "run simulation",
            "simulate",
            "call tool",
            "test these parameters",
            "execute",
            "continue the next round",
        ],
    )


def has_explicit_web_search_request(user_message: str) -> bool:
    """Detect explicit requests for external web/native Qwen search."""

    return _search_intent(user_message) is not None and not _forbids_web_search(user_message)


def _search_intent(user_message: str) -> str | None:
    text = (user_message or "").lower()
    compact = text.replace(" ", "")
    if _has_any(compact, ["最近的论文", "找文献", "找论文", "论文依据", "文献依据", "参考文献"]) or _has_any(
        text, ["recent paper", "latest paper", "find papers", "literature search", "citation", "references"]
    ):
        return "literature_search"
    if _has_any(compact, ["官方文档", "查文档", "api文档", "接口文档"]) or _has_any(
        text, ["official docs", "official documentation", "api docs", "documentation"]
    ):
        return "documentation_lookup"
    if _has_any(compact, ["查最新", "最新版本", "核实一下", "最新资料", "最近", "2025", "2026"]) or _has_any(
        text, ["latest", "current", "recent", "verify", "fact-check", "up to date", "2025", "2026"]
    ):
        return "current_info_lookup"
    if _has_any(compact, ["联网搜", "联网搜索", "上网查", "网上有没有", "网上查", "在线搜索"]) or _has_any(
        text, ["web search", "search online", "browse web", "look online", "online sources"]
    ):
        return "web_research"
    return None


def _forbids_web_search(user_message: str) -> bool:
    text = (user_message or "").lower()
    compact = text.replace(" ", "")
    return _has_any(compact, ["不要联网", "别联网", "不用联网", "只根据已有知识", "不要上网"]) or _has_any(
        text, ["do not search", "don't search", "no web", "without web", "only existing knowledge"]
    )


def _looks_like_fluid_research(text: str, compact: str) -> bool:
    return _has_any(
        compact,
        [
            "优化",
            "流场",
            "仿真",
            "流固耦合",
            "控制闭环",
            "边界条件",
            "目标函数",
            "机器鱼",
            "软体",
            "游动",
            "雷诺数",
            "低雷诺",
            "翼型",
            "微流控",
            "阻力",
            "换热",
            "多孔介质",
            "navier",
            "cfd",
            "freeflow",
        ],
    ) or _has_any(
        text,
        ["optimize", "flow", "simulation", "cfd", "fsi", "airfoil", "microfluidic", "drag", "heat transfer"],
    )


def _has_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def _hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
