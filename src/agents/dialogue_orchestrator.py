"""Qwen-powered dialogue orchestrator for FlowScientist."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.config import settings
from src.llm import get_llm_provider
from src.llm.base import LLMProvider
from src.state.conversation_state import ConversationState
from src.tools import get_default_tools
from src.tools.base import Tool
from src.utils.io import ensure_dir, write_json
from src.utils.llm_audit import parse_llm_json


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

    def handle_user_message(self, message: str) -> ConversationState:
        """Append user input, ask Qwen what to do, execute tools if requested."""

        self.state.append_message("user", message)
        decision = self._ask_qwen(user_message=message, phase="dialogue_decision")
        if decision.get("next_action") != "call_tool" and self._looks_like_concrete_goal(message):
            decision = self._ask_qwen(
                user_message=(
                    "The user's goal is concrete enough for an initial virtual experiment. "
                    "Generate a Qwen-designed tool_call now with 4 to 6 candidates. "
                    f"Original user message: {message}"
                ),
                phase="force_experiment_planning",
            )
        if self._needs_candidate_repair(decision):
            decision = self._ask_qwen(
                user_message=(
                    "Your previous tool_call did not include enough candidate designs. "
                    "Return next_action='call_tool' with tool_name='run_soft_swimmer_experiment' "
                    "and 4 to 6 candidate designs inside arguments.candidates. "
                    f"Original user message: {message}"
                ),
                phase="repair_tool_candidates",
            )
        self._apply_decision(decision)

        if decision.get("next_action") == "call_tool" and decision.get("tool_call"):
            tool_result = self._execute_tool(decision["tool_call"])
            self.state.append_message(
                "tool",
                json.dumps(tool_result, ensure_ascii=False),
                tool_name=decision["tool_call"].get("tool_name"),
            )
            follow_up = self._ask_qwen(
                user_message=(
                    "A tool call has completed. Analyze this result and decide the next step: "
                    f"{json.dumps(tool_result, ensure_ascii=False)}"
                ),
                phase="analyze_result",
            )
            self._apply_decision(follow_up)

        self._refresh_audit_counts()
        self.state.save()
        return self.state

    def run_three_rounds(self, initial_goal: str) -> ConversationState:
        """Convenience workflow driven through the dialogue orchestrator."""

        self.handle_user_message(initial_goal)
        for _ in range(2):
            self.handle_user_message(
                "Continue with the next experiment round using the latest tool result."
            )
        return self.state

    def _ask_qwen(self, user_message: str, phase: str) -> dict[str, Any]:
        """Call Qwen and parse strict JSON dialogue decision."""

        metadata = self.llm.metadata()
        if settings.qwen_require_real and metadata.get("is_mock", True):
            raise RuntimeError("Real Qwen is required. Mock fallback is disabled.")
        system_prompt = self._system_prompt()
        prompt = self._user_prompt(user_message, phase)
        raw = self._record_llm_call(system_prompt, prompt)
        decision = self._parse_or_repair_decision(raw, user_message, phase)
        return self._normalize_decision(decision)

    def _parse_or_repair_decision(
        self, raw_response: str, user_message: str, phase: str
    ) -> dict[str, Any]:
        """Parse Qwen JSON, repair once with real Qwen, then return safe JSON."""

        try:
            return parse_llm_json(raw_response, "DialogueOrchestrator")
        except Exception:
            repair_system_prompt = (
                "You are a JSON repair assistant for FlowScientist. Convert the "
                "previous assistant text into valid DialogueOrchestrator JSON only. "
                "Do not call tools unless the original user message clearly requests "
                "an experiment."
            )
            repair_user_prompt = f"""
Original phase: {phase}
Original user message: {user_message}
Previous non-JSON assistant content:
{raw_response}

Return strict JSON with this schema:
{{
  "assistant_message": "...",
  "state_update": {{}},
  "next_action": "ask_clarification|propose_plan|call_tool|analyze_result|generate_report",
  "tool_call": {{}}
}}
"""
            try:
                repaired = self._record_llm_call(repair_system_prompt, repair_user_prompt)
                return parse_llm_json(repaired, "DialogueOrchestrator")
            except Exception:
                return self._safe_clarification_decision(user_message, raw_response)

    def _safe_clarification_decision(
        self, user_message: str, raw_response: str
    ) -> dict[str, Any]:
        """Local safe JSON wrapper after real-Qwen repair failed."""

        if self._is_greeting_or_meta_question(user_message):
            assistant_message = (
                "你好，我是 FlowScientist，可以帮助你围绕软体游动机器人流场优化来澄清目标、"
                "规划实验、调用仿真工具并根据结果迭代。请告诉我你的研究目标、约束或已有数据。"
            )
        else:
            assistant_message = (
                "I need one more clarification before planning an experiment. "
                "Please specify whether you prioritize swimming speed, energy cost, "
                "stability, or efficiency, and any constraints you want to enforce."
            )
        return {
            "assistant_message": assistant_message,
            "state_update": {},
            "next_action": "ask_clarification",
            "tool_call": {},
            "repair_note": raw_response[:300],
        }

    def _system_prompt(self) -> str:
        tool_specs = {
            name: {"description": tool.description, "schema": tool.schema}
            for name, tool in self.tools.items()
        }
        return f"""
You are FlowScientist, a Qwen-powered conversational AI Scientist for soft robotic swimmer flow-field optimization.
You control the scientific loop: clarify goals, plan experiments, call tools, analyze tool results, revise plans, and generate reports.

Available tools:
{json.dumps(tool_specs, ensure_ascii=False)}

Return strict JSON only. No markdown. Schema:
{{
  "assistant_message": "natural language reply to user",
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
    "tool_name": "run_soft_swimmer_experiment",
    "arguments": {{}}
  }}
}}

If the user's goal is concrete enough, choose next_action="call_tool" and call run_soft_swimmer_experiment.
Do not ask clarification when the user already states an optimization direction such as maximizing speed, minimizing energy, improving efficiency, or prioritizing stability.
If the user only greets you or asks what you can do, respond naturally as FlowScientist and ask for the soft-swimmer research goal, constraints, or available data. Do not call a tool for a greeting.
For high-speed goals, use higher frequency/amplitude candidates.
For low-energy goals, use lower frequency/amplitude candidates.
For stability-first goals, use lower amplitude and higher stiffness candidates.
All tool candidate parameters must obey these bounds:
- amplitude: 0.05 to 0.50
- frequency: 0.5 to 3.0
- wavelength: 0.6 to 2.0
- stiffness: 0.1 to 1.0
- phase: 0.0 to 1.0
"""

    def _user_prompt(self, user_message: str, phase: str) -> str:
        state_snapshot = {
            "research_goal": self.state.research_goal,
            "constraints": self.state.constraints,
            "target_metric": self.state.target_metric,
            "priority_weights": self.state.priority_weights,
            "planning_preference": self.state.planning_preference,
            "experiment_history": self.state.experiment_history[-3:],
            "current_plan": self.state.current_plan,
            "messages": self.state.messages[-8:],
        }
        return (
            f"phase={phase}\n"
            f"user_message={user_message}\n"
            f"current_state={json.dumps(state_snapshot, ensure_ascii=False)}"
        )

    def _record_llm_call(self, system_prompt: str, user_prompt: str) -> str:
        index = self._next_index(self.llm_calls_dir, "*_request.json")
        metadata = self.llm.metadata()
        prompt = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}"
        request_payload = {
            "agent": "DialogueOrchestrator",
            "provider": metadata.get("provider"),
            "transport": metadata.get("transport"),
            "model": metadata.get("model"),
            "is_mock": bool(metadata.get("is_mock", True)),
            "prompt": prompt,
            "prompt_sha256": _hash(prompt),
            "timestamp": _timestamp(),
        }
        write_json(self.llm_calls_dir / f"{index:03d}_request.json", request_payload)
        raw = self.llm.generate(system_prompt, user_prompt) or ""
        if settings.qwen_require_real and not raw.strip():
            raise RuntimeError("Qwen returned an empty dialogue response.")
        response_payload = {
            "agent": "DialogueOrchestrator",
            "provider": metadata.get("provider"),
            "transport": metadata.get("transport"),
            "model": metadata.get("model"),
            "is_mock": bool(metadata.get("is_mock", True)),
            "raw_response": raw,
            "response_sha256": _hash(raw),
            "timestamp": _timestamp(),
        }
        write_json(self.llm_calls_dir / f"{index:03d}_response.json", response_payload)
        self.state.last_qwen_response_excerpt = raw[:300]
        return raw

    def _apply_decision(self, decision: dict[str, Any]) -> None:
        self.state.apply_state_update(decision.get("state_update", {}))
        assistant_message = decision.get("assistant_message") or ""
        if assistant_message:
            self.state.append_message(
                "assistant",
                assistant_message,
                next_action=decision.get("next_action"),
                tool_call=decision.get("tool_call"),
            )

    def _execute_tool(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        tool_name = tool_call.get("tool_name")
        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")
        arguments = tool_call.get("arguments") or {}
        index = self._next_index(self.tool_calls_dir, "*_request.json")
        request_path = self.tool_calls_dir / f"{index:03d}_{tool_name}_request.json"
        response_path = self.tool_calls_dir / f"{index:03d}_{tool_name}_response.json"
        request_payload = {
            "tool_name": tool_name,
            "arguments": arguments,
            "timestamp": _timestamp(),
        }
        write_json(request_path, request_payload)
        result = self.tools[tool_name].run(arguments)
        if tool_name == "generate_research_plan_report" and result.get("markdown"):
            self.state.final_report = result["markdown"]
            (Path(self.state.run_dir) / "final_report.md").write_text(
                result["markdown"], encoding="utf-8"
            )
        response_payload = {
            "tool_name": tool_name,
            "result": result,
            "result_sha256": _hash(json.dumps(result, ensure_ascii=False, default=str)),
            "timestamp": _timestamp(),
        }
        write_json(response_path, response_payload)
        self.state.tool_calls.append(response_payload)
        self.state.experiment_history.append(result)
        self.state.last_tool_result = result
        self.state.total_tool_calls = len(self.state.tool_calls)
        return result

    def _refresh_audit_counts(self) -> None:
        self.state.total_llm_calls = len(list(self.llm_calls_dir.glob("*_response.json")))
        self.state.total_tool_calls = len(list(self.tool_calls_dir.glob("*_response.json")))
        self.state.llm_backend = {
            "llm_provider": self.llm.metadata().get("provider"),
            "llm_transport": self.llm.metadata().get("transport"),
            "llm_model": self.llm.metadata().get("model"),
            "llm_base_url": self.llm.metadata().get("base_url"),
            "is_mock": bool(self.llm.metadata().get("is_mock", True)),
        }
        self._write_current_report()

    def _write_current_report(self) -> None:
        """Write a continuously updated audit-friendly Markdown report."""

        report = "\n".join(
            [
                "# FlowScientist Conversation Report",
                "",
                "## LLM Backend",
                f"- Provider: {self.state.llm_backend.get('llm_provider')}",
                f"- Model: {self.state.llm_backend.get('llm_model')}",
                f"- Transport: {self.state.llm_backend.get('llm_transport')}",
                f"- Mock mode: {str(self.state.llm_backend.get('is_mock', True)).lower()}",
                f"- Total LLM calls: {self.state.total_llm_calls}",
                f"- Total tool calls: {self.state.total_tool_calls}",
                f"- LLM call logs: {self.llm_calls_dir}",
                f"- Tool call logs: {self.tool_calls_dir}",
                "",
                "## Research Goal",
                str(self.state.research_goal or "Not clarified yet"),
                "",
                "## Current Planning State",
                f"- Target metric: {self.state.target_metric}",
                f"- Planning preference: {self.state.planning_preference}",
                f"- Priority weights: {self.state.priority_weights}",
                "",
                "## Conversation Messages",
                "\n".join(
                    f"- {message['role']}: {message['content']}"
                    for message in self.state.messages[-12:]
                ),
                "",
                "## Experiment History",
                json.dumps(self.state.experiment_history, ensure_ascii=False, indent=2, default=str),
            ]
        )
        self.state.final_report = report
        (Path(self.state.run_dir) / "final_report.md").write_text(report, encoding="utf-8")

    def _normalize_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        decision.setdefault("assistant_message", "")
        decision.setdefault("state_update", {})
        decision.setdefault("next_action", "ask_clarification")
        if decision.get("tool_call") is None:
            decision["tool_call"] = {}
        return decision

    def _looks_like_concrete_goal(self, message: str) -> bool:
        """Detect goals that should trigger an initial experiment plan."""

        text = message.lower()
        keywords = [
            "maximize",
            "minimize",
            "optimize",
            "improve",
            "speed",
            "energy",
            "stable",
            "stability",
            "efficiency",
            "提高",
            "降低",
            "优化",
            "稳定",
            "能耗",
        ]
        return any(keyword in text for keyword in keywords)

    def _is_greeting_or_meta_question(self, message: str) -> bool:
        """Detect greetings and capability questions."""

        text = message.lower().strip()
        greetings = ["你好", "hello", "hi", "hey", "你能做什么", "what can you do"]
        return any(item in text for item in greetings)

    def _needs_candidate_repair(self, decision: dict[str, Any]) -> bool:
        """Require a useful batch when Qwen chooses the simulator tool."""

        if decision.get("next_action") != "call_tool":
            return False
        tool_call = decision.get("tool_call") or {}
        if tool_call.get("tool_name") != "run_soft_swimmer_experiment":
            return False
        candidates = (tool_call.get("arguments") or {}).get("candidates") or []
        return len(candidates) < 4

    def _next_index(self, directory: Path, pattern: str) -> int:
        return len(list(directory.glob(pattern))) + 1


def _hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
