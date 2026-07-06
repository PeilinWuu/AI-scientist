"""Persistent conversation state for chat-first FlowScientist."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.utils.io import ensure_dir, read_json, write_json


@dataclass
class ConversationState:
    """Run-local state for the dialogue AI Scientist."""

    run_id: str
    run_dir: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    research_goal: str | None = None
    constraints: dict[str, Any] = field(default_factory=dict)
    target_metric: str | None = None
    priority_weights: dict[str, float] = field(default_factory=dict)
    planning_preference: str | None = None
    experiment_history: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    current_plan: dict[str, Any] = field(default_factory=dict)
    final_report: str | None = None
    llm_backend: dict[str, Any] = field(default_factory=dict)
    current_intent: str | None = None
    current_skill: str | None = None
    tool_execution_allowed: bool = False
    last_tool_permission: dict[str, Any] = field(default_factory=dict)
    last_decision_trace: dict[str, Any] = field(default_factory=dict)
    raw_data: list[dict[str, Any]] = field(default_factory=list)
    total_llm_calls: int = 0
    total_tool_calls: int = 0
    last_qwen_response_excerpt: str = ""
    last_tool_result: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return Path(self.run_dir) / "conversation.json"

    @classmethod
    def create(cls, run_id: str, run_dir: Path, llm_backend: dict[str, Any]) -> "ConversationState":
        ensure_dir(run_dir)
        ensure_dir(run_dir / "llm_calls")
        ensure_dir(run_dir / "tool_calls")
        state = cls(run_id=run_id, run_dir=str(run_dir), llm_backend=llm_backend)
        state.save()
        return state

    @classmethod
    def load(cls, run_dir: Path) -> "ConversationState":
        data = read_json(run_dir / "conversation.json")
        return cls(**data)

    def save(self) -> None:
        data = asdict(self)
        write_json(self.path, data)
        metadata = {
            **self.llm_backend,
            "total_llm_calls": self.total_llm_calls,
            "total_tool_calls": self.total_tool_calls,
            "current_intent": self.current_intent,
            "current_skill": self.current_skill,
            "tool_execution_allowed": self.tool_execution_allowed,
            "last_tool_permission": self.last_tool_permission,
            "last_decision_trace": self.last_decision_trace,
            "llm_calls_path": str(Path(self.run_dir) / "llm_calls"),
            "tool_calls_path": str(Path(self.run_dir) / "tool_calls"),
            "last_llm_response_excerpt": self.last_qwen_response_excerpt,
            "is_mock": bool(self.llm_backend.get("is_mock", True)),
        }
        write_json(Path(self.run_dir) / "metadata.json", metadata)
        write_json(Path(self.run_dir) / "config.json", {**data, **metadata})

    def append_message(self, role: str, content: str, **extra: Any) -> None:
        message = {"role": role, "content": content}
        message.update(extra)
        self.messages.append(message)
        self.save()

    def apply_state_update(self, update: dict[str, Any]) -> None:
        if not update:
            return
        for key in [
            "research_goal",
            "constraints",
            "target_metric",
            "priority_weights",
            "planning_preference",
            "current_plan",
            "final_report",
            "current_intent",
            "current_skill",
        ]:
            if key in update and update[key] is not None:
                setattr(self, key, update[key])
        if "tool_execution_allowed" in update:
            self.tool_execution_allowed = bool(update["tool_execution_allowed"])
        self.save()
