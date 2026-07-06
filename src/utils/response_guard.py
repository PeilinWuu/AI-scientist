"""Guards against repetitive or wrong-skill assistant responses."""

from __future__ import annotations

from difflib import SequenceMatcher


CAPABILITY_STARTS = (
    "FlowScientist is",
    "FlowScientist 是",
    "我是 FlowScientist",
    "我是FlowScientist",
)

GENERIC_CAPABILITY_MARKERS = (
    "对话式 AI Scientist",
    "对话式AI科学家",
    "可以帮助你",
    "current prototype",
    "currently implemented",
)


def needs_response_rewrite(
    assistant_message: str,
    previous_assistant_message: str | None,
    intent: str,
) -> tuple[bool, str]:
    """Return whether a response should be rewritten for this intent."""

    message = (assistant_message or "").strip()
    previous = (previous_assistant_message or "").strip()
    if not message:
        return False, ""

    if intent != "capability_question" and message.startswith(CAPABILITY_STARTS):
        return True, "non-capability response starts with capability introduction"

    if intent in {"research_consultation", "experiment_planning"}:
        marker_hits = sum(1 for marker in GENERIC_CAPABILITY_MARKERS if marker in message)
        research_hits = sum(
            1
            for marker in ["流固耦合", "约束", "目标函数", "设计变量", "FreeFlow", "CFD", "下一步"]
            if marker in message
        )
        if marker_hits >= 2 and research_hits < 2:
            return True, "research response looks like generic capability introduction"

    if previous and intent != "capability_question":
        similarity = SequenceMatcher(None, previous[:1200], message[:1200]).ratio()
        if similarity > 0.82:
            return True, f"assistant response too similar to previous message ({similarity:.2f})"

    return False, ""


def rewrite_prompt(user_message: str, bad_message: str, intent: str, reason: str) -> tuple[str, str]:
    """Build a Qwen prompt that rewrites a bad user-facing response."""

    system_prompt = (
        "You are a response repair module for FlowScientist. Return plain natural language only. "
        "Do not return JSON. Do not introduce FlowScientist unless the intent is capability_question."
    )
    user_prompt = f"""
Intent: {intent}
Rewrite reason: {reason}
Latest user message:
{user_message}

Previous bad assistant message:
{bad_message}

Instructions:
- Do not repeat your capability introduction unless the latest user message asks about capability, advantages, role, or scope.
- For concrete research tasks, analyze the user's task and propose next steps.
- For complex soft-swimmer/FSI/Navier-Stokes/material fatigue problems, say the current lightweight soft-swimmer tool cannot directly solve the full CFD/FSI/material-fatigue constraints.
- Include next choices when relevant: A. experiment task planning, B. CFD/FreeFlow adapter data-interface design, C. simplified soft-swimmer demo.
"""
    return system_prompt, user_prompt
