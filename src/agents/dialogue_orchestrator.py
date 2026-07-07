"""Qwen-powered dialogue orchestrator for FlowScientist."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.agents.intent_router import IntentRouter
from src.agents.response_composer import compose_user_response
from src.config import settings
from src.domain_knowledge import (
    PrincipleSelector,
    SelectedPrinciple,
    build_internal_domain_context,
    load_domain_principles,
)
from src.llm import get_llm_provider
from src.llm.base import LLMProvider
from src.policies.tool_use_policy import decide_tool_permission, resolve_user_controlled_search
from src.skills import READABLE_RESPONSE_SKILL, TOOL_POLICY_SKILL, get_skill_prompt
from src.state.conversation_state import ConversationState
from src.tools import get_default_tools
from src.tools.base import Tool
from src.utils.io import ensure_dir, write_json
from src.utils.llm_audit import parse_llm_json
from src.utils.readable_response import (
    ensure_readable_assistant_message,
    ensure_user_readable_response,
)
from src.utils.response_guard import needs_response_rewrite, rewrite_prompt


class DialogueOrchestrator:
    """Main chat-first AI Scientist controller."""

    def __init__(
        self,
        state: ConversationState,
        llm: LLMProvider | None = None,
        tools: dict[str, Tool] | None = None,
    ) -> None:
        self.state = state
        self.llm = llm or get_llm_provider()
        self.tools = tools or get_default_tools()
        self.llm_calls_dir = Path(state.run_dir) / "llm_calls"
        self.tool_calls_dir = Path(state.run_dir) / "tool_calls"
        ensure_dir(self.llm_calls_dir)
        ensure_dir(self.tool_calls_dir)
        if hasattr(self.llm, "set_debug_dir"):
            self.llm.set_debug_dir(self.llm_calls_dir)
        self.intent_router = IntentRouter(llm=self.llm, llm_calls_dir=self.llm_calls_dir)
        self.domain_principles = load_domain_principles()
        self.principle_selector = PrincipleSelector(self.domain_principles)
        self._latest_selected_principles: list[SelectedPrinciple] = []
        self._latest_qwen_search: dict[str, Any] = {
            "enable_search": False,
            "search_options": {},
            "search_trigger": "",
            "reason": "No search decision yet.",
        }
        self._latest_tool_permission: dict[str, Any] = {
            "allowed": False,
            "requires_confirmation": False,
            "reason": "No tool call proposed yet.",
            "user_facing_message": "",
        }

    def handle_user_message(self, message: str) -> ConversationState:
        """Route intent, ask Qwen, apply policy, and optionally execute a tool."""

        tool_calls_before = self.state.total_tool_calls
        self._latest_tool_permission = {
            "allowed": False,
            "requires_confirmation": False,
            "reason": "No tool call proposed.",
            "user_facing_message": "",
        }
        self._latest_qwen_search = {
            "enable_search": False,
            "search_options": {},
            "search_trigger": "",
            "reason": "No search decision yet.",
        }
        self.state.append_message("user", message)
        router_result = self.intent_router.classify(message, self._state_snapshot())
        intent = router_result["intent"]
        self._set_intent_state(intent, router_result)

        decision = self._ask_qwen(message, phase=f"{intent}_decision", intent=intent)
        decision = self._apply_tool_policy(decision, intent, message)

        if self._should_fill_default_tool_call(decision, intent):
            decision = self._default_tool_call_for_intent(intent, message)
            decision = self._apply_tool_policy(decision, intent, message)

        if self._has_tool_call(decision):
            tool_result = self._execute_tool(decision["tool_call"])
            tool_name = decision["tool_call"].get("tool_name", "")
            composed = compose_user_response(
                intent=intent,
                selected_skill=self.state.current_skill or "",
                user_message=message,
                skill_output=decision,
                tool_result=tool_result,
                tool_permission=self._latest_tool_permission,
                state=self.state,
            )
            self._append_composed_message(composed, tool_name=tool_name)
        else:
            self._apply_decision(decision, user_message=message)

        self._refresh_audit_counts()
        self._record_decision_trace(message, tool_calls_before)
        if self.state.web_search_mode == "this_turn":
            self.state.web_search_mode = "off"
        self.state.save()
        return self.state

    def run_three_rounds(self, initial_goal: str) -> ConversationState:
        """Convenience workflow with explicit execution authorization."""

        self.handle_user_message(initial_goal)
        self.handle_user_message("请运行一次实验，测试你建议的参数。")
        for _ in range(2):
            self.handle_user_message("请根据刚才结果继续下一轮实验。")
        return self.state

    def _set_intent_state(self, intent: str, router_result: dict[str, Any]) -> None:
        self.state.current_intent = intent
        self.state.current_skill = self._skill_name_for_intent(intent)
        self.state.tool_execution_allowed = False
        self.state.raw_data.append(
            {"source": "intent_router", "data": router_result, "timestamp": _timestamp()}
        )
        self.state.save()

    def _ask_qwen(self, user_message: str, phase: str, intent: str) -> dict[str, Any]:
        metadata = self.llm.metadata()
        if settings.qwen_require_real and metadata.get("is_mock", True):
            raise RuntimeError("Real Qwen is required. Mock fallback is disabled.")
        self._latest_selected_principles = self.principle_selector.select(
            user_message=user_message,
            intent=intent,
            research_goal=self.state.research_goal,
            constraints=self.state.constraints,
            top_k=4,
        )
        user_requested_references = _user_requested_references(user_message)
        domain_context = build_internal_domain_context(
            self._latest_selected_principles,
            user_requested_references=user_requested_references,
        )
        self.state.raw_data.append(
            {
                "source": "domain_knowledge",
                "data": {
                    "selected_principles": self._selected_principle_audit(),
                    "user_requested_references": user_requested_references,
                },
                "timestamp": _timestamp(),
            }
        )
        search_permission = resolve_user_controlled_search(
            web_search_mode=self.state.web_search_mode,
            user_message=user_message,
        )
        self._latest_qwen_search = {
            "enable_search": bool(search_permission.get("enable_search")),
            "search_options": dict(search_permission.get("search_options") or {}),
            "search_trigger": str(search_permission.get("search_trigger") or ""),
            "reason": str(search_permission.get("reason") or ""),
            "provider": metadata.get("provider"),
        }
        self.state.qwen_web_search_enabled = settings.qwen_enable_search
        self.state.last_user_search_choice = self.state.web_search_mode
        self.state.last_qwen_search_used = bool(self._latest_qwen_search["enable_search"])
        self.state.last_search_trigger = self._latest_qwen_search["search_trigger"]
        self.state.last_search_options = self._latest_qwen_search["search_options"]
        system_prompt = self._system_prompt(intent, domain_context)
        user_prompt = self._user_prompt(user_message, phase, intent)
        raw = self._record_llm_call(
            system_prompt,
            user_prompt,
            agent="DialogueOrchestrator",
            llm_call_options=self._latest_qwen_search,
        )
        decision = self._parse_or_repair_decision(raw, user_message, phase, intent)
        decision["llm_call_options"] = self._latest_qwen_search
        return self._normalize_decision(decision)

    def _parse_or_repair_decision(
        self, raw_response: str, user_message: str, phase: str, intent: str
    ) -> dict[str, Any]:
        try:
            return parse_llm_json(raw_response, "DialogueOrchestrator")
        except Exception:
            repair_system_prompt = (
                "You are a JSON repair assistant for FlowScientist. Convert the previous "
                "assistant text into valid DialogueOrchestrator JSON only."
            )
            repair_user_prompt = f"""
Original phase: {phase}
Classified intent: {intent}
Original user message: {user_message}
Previous non-JSON assistant content:
{raw_response}

Return strict JSON:
{{
  "assistant_message": "...",
  "state_update": {{}},
  "next_action": "ask_clarification|propose_plan|call_tool|analyze_result|generate_report",
  "tool_call": {{}}
}}
"""
            try:
                repaired = self._record_llm_call(
                    repair_system_prompt, repair_user_prompt, agent="DialogueOrchestratorRepair"
                )
                return parse_llm_json(repaired, "DialogueOrchestrator")
            except Exception:
                return self._safe_decision(intent, raw_response)

    def _safe_decision(self, intent: str, raw_response: str) -> dict[str, Any]:
        if intent == "research_consultation":
            message = (
                "这是一个流体仿真与优化研究咨询问题。我会先帮助你拆解物理模型、设计变量、"
                "目标函数、约束条件和仿真数据接口；当前不会直接运行 soft-swimmer 轻量示例工具。"
            )
        elif intent in {"capability_question", "casual_chat"}:
            message = (
                "我是 FlowScientist，一个面向通用流体仿真与流场优化的对话式 AI Scientist。"
                "当前已实现的可执行工具是 soft-swimmer 轻量虚拟实验工具，其他 CFD/FreeFlow "
                "和实验仪器接口属于可扩展方向。"
            )
        else:
            message = "我会先给出可读的分析和下一步建议；只有在你明确授权时才调用工具。"
        return {
            "assistant_message": message,
            "state_update": {},
            "next_action": "ask_clarification",
            "tool_call": {},
            "raw_data": {"repair_note": raw_response[:300]},
        }

    def _system_prompt(self, intent: str, domain_context: str = "") -> str:
        tool_specs = {
            name: {"description": tool.description, "schema": tool.schema}
            for name, tool in self.tools.items()
        }
        return f"""
You are FlowScientist, a conversational AI Scientist for general fluid simulation and flow-field optimization.

The currently implemented executable experiment tool is a lightweight soft-swimmer virtual experiment tool.
It is not a real CFD, Navier-Stokes, or FSI solver. Other domains such as airfoil optimization,
pipe-flow drag reduction, microfluidic mixing, porous-media flow, vortex-shedding drag reduction,
heat-transfer optimization, and hull/underwater-vehicle drag optimization are extensible directions,
not all implemented tools.

Current intent: {intent}
Current skill:
{get_skill_prompt(intent)}

{domain_context}

Tool policy:
{TOOL_POLICY_SKILL}

Readable response policy:
{READABLE_RESPONSE_SKILL}

Available tools:
{json.dumps(tool_specs, ensure_ascii=False)}

Return strict JSON only:
{{
  "assistant_message": "natural language reply to user, never raw JSON",
  "state_update": {{
    "research_goal": "...",
    "constraints": {{}},
    "target_metric": "efficiency|mean_speed|energy_cost|stability_score",
    "priority_weights": {{"mean_speed": 0.0, "energy_cost": 0.0, "efficiency": 0.0, "stability_score": 0.0}},
    "planning_preference": "high_speed|low_energy|stability_first|balanced_efficiency",
    "current_plan": {{}},
    "final_report": "..."
  }},
  "next_action": "ask_clarification|propose_plan|call_tool|analyze_result|generate_report",
  "tool_call": {{
    "tool_name": "run_soft_swimmer_experiment|generate_experiment_plot|generate_research_plan_report",
    "arguments": {{}}
  }}
}}

For complex research goals, do not call tools immediately. First explain the problem,
separate prototype capability from real CFD/FSI needs, propose next steps, and ask the user whether to:
A. generate an experiment task plan, B. run the simplified soft-swimmer demo, or C. design a CFD adapter data interface.

Do not repeat your capability introduction unless the latest user message explicitly asks
about your capability, advantages, role, or scope. For concrete research tasks, respond
with problem analysis and next-step recommendations. The latest user message has highest priority.
"""

    def _user_prompt(self, user_message: str, phase: str, intent: str) -> str:
        return (
            f"phase={phase}\n"
            f"classified_intent={intent}\n"
            f"user_message={user_message}\n"
            f"current_state={json.dumps(self._state_snapshot(), ensure_ascii=False)}"
        )

    def _record_llm_call(
        self,
        system_prompt: str,
        user_prompt: str,
        agent: str,
        llm_call_options: dict[str, Any] | None = None,
    ) -> str:
        index = self._next_index(self.llm_calls_dir, "*_request.json")
        metadata = self.llm.metadata()
        llm_call_options = llm_call_options or {
            "enable_search": False,
            "search_options": {},
            "search_trigger": "",
        }
        prompt = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}"
        write_json(
            self.llm_calls_dir / f"{index:03d}_request.json",
            {
                "agent": agent,
                "provider": metadata.get("provider"),
                "transport": metadata.get("transport"),
                "model": metadata.get("model"),
                "is_mock": bool(metadata.get("is_mock", True)),
                "prompt": prompt,
                "prompt_sha256": _hash(prompt),
                "enable_search": bool(llm_call_options.get("enable_search")),
                "search_options": dict(llm_call_options.get("search_options") or {}),
                "search_trigger": str(llm_call_options.get("search_trigger") or ""),
                "timestamp": _timestamp(),
            },
        )
        raw = self.llm.generate(
            system_prompt,
            user_prompt,
            enable_search=bool(llm_call_options.get("enable_search")),
            search_options=dict(llm_call_options.get("search_options") or {}),
            search_trigger=str(llm_call_options.get("search_trigger") or ""),
        ) or ""
        if settings.qwen_require_real and not raw.strip():
            raise RuntimeError("Qwen returned an empty dialogue response.")
        write_json(
            self.llm_calls_dir / f"{index:03d}_response.json",
            {
                "agent": agent,
                "provider": metadata.get("provider"),
                "transport": metadata.get("transport"),
                "model": metadata.get("model"),
                "is_mock": bool(metadata.get("is_mock", True)),
                "raw_response": raw,
                "response_sha256": _hash(raw),
                "enable_search": bool(llm_call_options.get("enable_search")),
                "search_options": dict(llm_call_options.get("search_options") or {}),
                "search_trigger": str(llm_call_options.get("search_trigger") or ""),
                "timestamp": _timestamp(),
            },
        )
        self.state.last_qwen_response_excerpt = raw[:300]
        return raw

    def _apply_tool_policy(
        self, decision: dict[str, Any], intent: str, user_message: str
    ) -> dict[str, Any]:
        decision = self._normalize_decision(decision)
        if decision.get("next_action") != "call_tool":
            self._latest_tool_permission = {
                "allowed": False,
                "requires_confirmation": False,
                "reason": "No tool call proposed by the selected skill.",
                "user_facing_message": "",
            }
            self.state.last_tool_permission = self._latest_tool_permission
            return ensure_user_readable_response(decision)

        permission = decide_tool_permission(intent, user_message, self.state, decision.get("tool_call"))
        self._latest_tool_permission = permission
        self.state.last_tool_permission = permission
        self.state.tool_execution_allowed = bool(permission["allowed"])
        if permission["allowed"]:
            decision["tool_permission"] = permission
            return ensure_user_readable_response(decision)

        blocked_tool_call = decision.get("tool_call")
        decision["next_action"] = "propose_plan" if permission["requires_confirmation"] else "ask_clarification"
        decision["tool_call"] = {}
        if permission["user_facing_message"]:
            existing = ensure_readable_assistant_message(decision.get("assistant_message", ""))
            decision["assistant_message"] = (
                existing.rstrip() + "\n\n" + permission["user_facing_message"]
                if existing
                else permission["user_facing_message"]
            )
        decision["raw_data"] = {
            "blocked_tool_call": blocked_tool_call,
            "tool_permission": permission,
        }
        return ensure_user_readable_response(decision)

    def _apply_decision(self, decision: dict[str, Any], user_message: str = "") -> None:
        decision = ensure_user_readable_response(decision)
        update = decision.get("state_update", {}) or {}
        update["current_intent"] = self.state.current_intent
        update["current_skill"] = self.state.current_skill
        update["tool_execution_allowed"] = self.state.tool_execution_allowed
        self.state.apply_state_update(update)
        composed = compose_user_response(
            intent=self.state.current_intent or "",
            selected_skill=self.state.current_skill or "",
            user_message=user_message,
            skill_output=decision,
            tool_result=None,
            tool_permission=self._latest_tool_permission,
            state=self.state,
        )
        self._append_composed_message(composed)

    def _append_composed_message(
        self, composed: dict[str, Any], tool_name: str | None = None
    ) -> None:
        message = ensure_readable_assistant_message(composed.get("assistant_message", ""))
        message = self._guard_and_rewrite_message(message)
        if message:
            self.state.append_message(
                "assistant",
                message,
                sections=composed.get("sections", []),
                tables=composed.get("tables", []),
                figures=composed.get("figures", []),
                suggested_actions=composed.get("suggested_actions", []),
                search_used=bool(composed.get("search_used")),
                raw_debug=composed.get("raw_debug", {}),
                intent=self.state.current_intent,
                skill=self.state.current_skill,
                tool_name=tool_name,
            )
        if composed.get("raw_debug") is not None:
            self.state.raw_data.append(
                {
                    "source": "response_composer",
                    "intent": self.state.current_intent,
                    "data": composed.get("raw_debug"),
                    "timestamp": _timestamp(),
                }
            )

    def _execute_tool(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        tool_name = tool_call.get("tool_name")
        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")
        arguments = dict(tool_call.get("arguments") or {})
        if tool_name == "generate_experiment_plot":
            arguments.setdefault("run_dir", self.state.run_dir)
            arguments.setdefault("experiment_history", self.state.experiment_history)
            arguments.setdefault("figure_type", "efficiency_by_candidate")
        if tool_name == "generate_research_plan_report":
            arguments.setdefault("research_goal", self.state.research_goal)
            arguments.setdefault("planning_preference", self.state.planning_preference)
            arguments.setdefault("experiment_history", self.state.experiment_history)
            arguments.setdefault("llm_backend", self.state.llm_backend)

        index = self._next_index(self.tool_calls_dir, "*_request.json")
        request_path = self.tool_calls_dir / f"{index:03d}_{tool_name}_request.json"
        response_path = self.tool_calls_dir / f"{index:03d}_{tool_name}_response.json"
        write_json(request_path, {"tool_name": tool_name, "arguments": arguments, "timestamp": _timestamp()})
        result = self.tools[tool_name].run(arguments)
        if tool_name == "generate_research_plan_report" and result.get("markdown"):
            self.state.final_report = result["markdown"]
            (Path(self.state.run_dir) / "final_report.md").write_text(result["markdown"], encoding="utf-8")

        response_payload = {
            "tool_name": tool_name,
            "result": result,
            "result_sha256": _hash(json.dumps(result, ensure_ascii=False, default=str)),
            "timestamp": _timestamp(),
        }
        write_json(response_path, response_payload)
        self.state.tool_calls.append(response_payload)
        if tool_name == "run_soft_swimmer_experiment":
            self.state.experiment_history.append(result)
        self.state.raw_data.append({"source": tool_name, "data": result, "timestamp": _timestamp()})
        self.state.last_tool_result = result
        self.state.total_tool_calls = len(self.state.tool_calls)
        return result

    def _should_fill_default_tool_call(self, decision: dict[str, Any], intent: str) -> bool:
        return intent in {"tool_execution", "visualization_request", "report_generation"} and not self._has_tool_call(decision)

    def _default_tool_call_for_intent(self, intent: str, message: str) -> dict[str, Any]:
        if intent == "tool_execution":
            return self._default_soft_swimmer_tool_call(message)
        if intent == "visualization_request":
            return self._default_plot_tool_call()
        if intent == "report_generation":
            return {
                "assistant_message": "我将基于当前对话和实验历史生成阶段报告。",
                "state_update": {},
                "next_action": "call_tool",
                "tool_call": {"tool_name": "generate_research_plan_report", "arguments": {}},
            }
        return {"assistant_message": "", "state_update": {}, "next_action": "ask_clarification", "tool_call": {}}

    def _default_soft_swimmer_tool_call(self, message: str) -> dict[str, Any]:
        text = message.lower()
        if "energy" in text or "能耗" in message:
            candidates = [
                {"candidate_id": "C001", "amplitude": 0.16, "frequency": 0.8, "wavelength": 1.1, "stiffness": 0.65, "phase": 0.25},
                {"candidate_id": "C002", "amplitude": 0.20, "frequency": 1.0, "wavelength": 1.2, "stiffness": 0.70, "phase": 0.35},
                {"candidate_id": "C003", "amplitude": 0.24, "frequency": 1.2, "wavelength": 1.3, "stiffness": 0.60, "phase": 0.45},
                {"candidate_id": "C004", "amplitude": 0.18, "frequency": 0.9, "wavelength": 1.4, "stiffness": 0.75, "phase": 0.55},
            ]
        elif "speed" in text or "速度" in message:
            candidates = [
                {"candidate_id": "C001", "amplitude": 0.30, "frequency": 2.0, "wavelength": 1.0, "stiffness": 0.45, "phase": 0.20},
                {"candidate_id": "C002", "amplitude": 0.36, "frequency": 2.3, "wavelength": 1.1, "stiffness": 0.50, "phase": 0.35},
                {"candidate_id": "C003", "amplitude": 0.40, "frequency": 2.6, "wavelength": 1.2, "stiffness": 0.55, "phase": 0.50},
                {"candidate_id": "C004", "amplitude": 0.32, "frequency": 2.8, "wavelength": 1.4, "stiffness": 0.60, "phase": 0.65},
            ]
        else:
            candidates = [
                {"candidate_id": "C001", "amplitude": 0.22, "frequency": 1.1, "wavelength": 1.0, "stiffness": 0.55, "phase": 0.20},
                {"candidate_id": "C002", "amplitude": 0.26, "frequency": 1.4, "wavelength": 1.2, "stiffness": 0.60, "phase": 0.35},
                {"candidate_id": "C003", "amplitude": 0.30, "frequency": 1.7, "wavelength": 1.4, "stiffness": 0.50, "phase": 0.50},
                {"candidate_id": "C004", "amplitude": 0.34, "frequency": 2.0, "wavelength": 1.6, "stiffness": 0.65, "phase": 0.65},
            ]
        return {
            "assistant_message": "你已明确授权运行实验。我将调用当前内置的 soft-swimmer 轻量示例工具，并在完成后解释结果。",
            "state_update": {"planning_preference": self.state.planning_preference or "balanced_efficiency"},
            "next_action": "call_tool",
            "tool_call": {
                "tool_name": "run_soft_swimmer_experiment",
                "arguments": {"candidates": candidates, "constraints": self.state.constraints or {}, "random_seed": 42},
            },
        }

    def _default_plot_tool_call(self) -> dict[str, Any]:
        return {
            "assistant_message": "我将基于已有实验历史生成效率柱状图，并在聊天中显示图像。",
            "state_update": {},
            "next_action": "call_tool",
            "tool_call": {"tool_name": "generate_experiment_plot", "arguments": {"figure_type": "efficiency_by_candidate"}},
        }

    def _has_tool_call(self, decision: dict[str, Any]) -> bool:
        call = decision.get("tool_call") or {}
        return decision.get("next_action") == "call_tool" and bool(call.get("tool_name"))

    def _state_snapshot(self) -> dict[str, Any]:
        return {
            "research_goal": self.state.research_goal,
            "constraints": self.state.constraints,
            "target_metric": self.state.target_metric,
            "priority_weights": self.state.priority_weights,
            "planning_preference": self.state.planning_preference,
            "current_intent": self.state.current_intent,
            "current_skill": self.state.current_skill,
            "tool_execution_allowed": self.state.tool_execution_allowed,
            "experiment_history": self.state.experiment_history[-3:],
            "current_plan": self.state.current_plan,
            "messages": self.state.messages[-8:],
        }

    def _guard_and_rewrite_message(self, message: str) -> str:
        previous = self._previous_assistant_message()
        should_rewrite, reason = needs_response_rewrite(
            message, previous, self.state.current_intent or ""
        )
        if not should_rewrite:
            return message
        system_prompt, user_prompt = rewrite_prompt(
            self.state.messages[-1]["content"] if self.state.messages else "",
            message,
            self.state.current_intent or "",
            reason,
        )
        try:
            repaired = self._record_llm_call(
                system_prompt, user_prompt, agent="ResponseGuard"
            )
            repaired = ensure_readable_assistant_message(repaired)
            second_check, _ = needs_response_rewrite(
                repaired, previous, self.state.current_intent or ""
            )
            if not second_check:
                self.state.raw_data.append(
                    {
                        "source": "response_guard",
                        "data": {"reason": reason, "original": message[:500]},
                        "timestamp": _timestamp(),
                    }
                )
                return repaired
        except Exception as exc:  # noqa: BLE001 - keep UI alive with deterministic fallback.
            self.state.raw_data.append(
                {
                    "source": "response_guard_error",
                    "data": {"reason": reason, "error": str(exc)},
                    "timestamp": _timestamp(),
                }
            )
        if self.state.current_intent == "research_consultation":
            return (
                "这是一个具体的流场优化研究任务，而不是能力介绍问题。我的理解是：你关注的是带有"
                "流固耦合、运动控制和约束优化特征的推进问题。当前内置的 soft-swimmer lightweight "
                "tool 不能直接求解完整 Navier-Stokes/FSI/材料疲劳约束。建议下一步先明确设计变量、"
                "目标函数、约束和边界条件，再选择 A. 实验任务规划，B. CFD/FreeFlow adapter 数据接口设计，"
                "或 C. 使用当前简化 soft-swimmer 工具做演示。"
            )
        return message

    def _previous_assistant_message(self) -> str | None:
        for item in reversed(self.state.messages[:-1]):
            if item.get("role") == "assistant":
                return str(item.get("content", ""))
        return None

    def _record_decision_trace(self, user_message: str, tool_calls_before: int) -> None:
        assistant_messages = [m for m in self.state.messages if m.get("role") == "assistant"]
        assistant_message = assistant_messages[-1]["content"] if assistant_messages else ""
        whether_tool_called = self.state.total_tool_calls > tool_calls_before
        trace = {
            "timestamp": _timestamp(),
            "user_message": user_message,
            "detected_intent": self.state.current_intent,
            "selected_skill": self.state.current_skill,
            "tool_permission": self._latest_tool_permission,
            "qwen_search": self._latest_qwen_search,
            "whether_tool_called": whether_tool_called,
            "assistant_message": assistant_message,
            "reasoning_summary": (
                f"intent={self.state.current_intent}; skill={self.state.current_skill}; "
                f"tool_allowed={self._latest_tool_permission.get('allowed')}; "
                f"tool_called={whether_tool_called}"
            ),
            "latest_llm_response_excerpt": self.state.last_qwen_response_excerpt,
            "selected_principles": self._selected_principle_audit(),
        }
        self.state.last_decision_trace = trace
        self.state.last_tool_permission = self._latest_tool_permission
        path = Path(self.state.run_dir) / "decision_trace.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace, ensure_ascii=False, default=str) + "\n")

    def _refresh_audit_counts(self) -> None:
        self.state.total_llm_calls = len(list(self.llm_calls_dir.glob("*_response.json")))
        self.state.total_tool_calls = len(list(self.tool_calls_dir.glob("*_response.json")))
        self.state.llm_backend = {
            "llm_provider": self.llm.metadata().get("provider"),
            "llm_transport": self.llm.metadata().get("transport"),
            "llm_model": self.llm.metadata().get("model"),
            "llm_base_url": self.llm.metadata().get("base_url"),
            "is_mock": bool(self.llm.metadata().get("is_mock", True)),
            "qwen_web_search_enabled": settings.qwen_enable_search,
            "last_qwen_search_used": self.state.last_qwen_search_used,
            "last_search_trigger": self.state.last_search_trigger,
            "last_search_options": self.state.last_search_options,
        }
        self._write_current_report()

    def _write_current_report(self) -> None:
        report = "\n".join(
            [
                "# FlowScientist Conversation Report",
                "",
                "FlowScientist is a conversational AI Scientist for general fluid simulation and flow-field optimization. The current executable demo tool is a soft-swimmer virtual experiment tool.",
                "",
                "## LLM Backend",
                f"- Provider: {self.state.llm_backend.get('llm_provider')}",
                f"- Model: {self.state.llm_backend.get('llm_model')}",
                f"- Transport: {self.state.llm_backend.get('llm_transport')}",
                f"- Mock mode: {str(self.state.llm_backend.get('is_mock', True)).lower()}",
                f"- Total LLM calls: {self.state.total_llm_calls}",
                f"- Total tool calls: {self.state.total_tool_calls}",
                "",
                "## Current Intent State",
                f"- Intent: {self.state.current_intent}",
                f"- Skill: {self.state.current_skill}",
                f"- Tool execution allowed: {self.state.tool_execution_allowed}",
                "",
                "## Research Goal",
                str(self.state.research_goal or "Not clarified yet"),
                "",
                "## Conversation Messages",
                "\n".join(f"- {m['role']}: {m['content']}" for m in self.state.messages[-12:]),
                "",
                "## Experiment History",
                json.dumps(self.state.experiment_history, ensure_ascii=False, indent=2, default=str),
            ]
        )
        self.state.final_report = report
        (Path(self.state.run_dir) / "final_report.md").write_text(report, encoding="utf-8")

    def _normalize_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        decision = dict(decision or {})
        decision.setdefault("assistant_message", "")
        decision.setdefault("state_update", {})
        decision.setdefault("next_action", "ask_clarification")
        if decision.get("tool_call") is None:
            decision["tool_call"] = {}
        return decision

    def _skill_name_for_intent(self, intent: str) -> str:
        mapping = {
            "casual_chat": "base_dialogue_skill",
            "capability_question": "capability_skill",
            "conceptual_explanation": "conceptual_explanation_skill",
            "research_consultation": "research_consultation_skill",
            "experiment_planning": "experiment_planning_skill",
            "tool_execution": "tool_execution_skill",
            "result_analysis": "result_analysis_skill",
            "visualization_request": "visualization_skill",
            "report_generation": "report_skill",
            "web_research": "research_consultation_skill",
            "literature_search": "research_consultation_skill",
            "documentation_lookup": "research_consultation_skill",
            "current_info_lookup": "research_consultation_skill",
        }
        return mapping.get(intent, "base_dialogue_skill")

    def _next_index(self, directory: Path, pattern: str) -> int:
        return len(list(directory.glob(pattern))) + 1

    def _selected_principle_audit(self) -> list[dict[str, Any]]:
        return [
            {
                "principle_id": item.principle_id,
                "domain": item.domain,
                "source_ids": item.source_ids,
                "confidence": item.confidence,
            }
            for item in self._latest_selected_principles
        ]


def _hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user_requested_references(user_message: str) -> bool:
    text = (user_message or "").lower()
    markers = ["论文", "文献", "参考", "references", "paper", "citation", "来源", "依据"]
    return any(marker in text for marker in markers)
