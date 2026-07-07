"""Keyword-based selection for distilled domain principles."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass
class SelectedPrinciple:
    """A selected domain principle safe for internal prompt injection."""

    principle_id: str
    domain: str
    internal_guidance: list[str]
    design_implications: list[str]
    forbidden_user_facing_phrases: list[str]
    source_ids: list[str]
    confidence: str


class PrincipleSelector:
    """Select top-matching domain principles with simple bilingual keyword scoring."""

    def __init__(self, principles: list[dict[str, Any]]) -> None:
        self.principles = principles

    def select(
        self,
        user_message: str,
        intent: str = "",
        research_goal: str | None = None,
        constraints: dict[str, Any] | None = None,
        top_k: int = 4,
    ) -> list[SelectedPrinciple]:
        """Return the top matching principles for the current dialogue context."""

        text = self._context_text(user_message, intent, research_goal, constraints)
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for index, principle in enumerate(self.principles):
            score = self._score_principle(principle, text, intent)
            if score > 0:
                scored.append((score, -index, principle))
        scored.sort(reverse=True)
        return [self._to_selected(item) for _, _, item in scored[:top_k]]

    def _context_text(
        self,
        user_message: str,
        intent: str,
        research_goal: str | None,
        constraints: dict[str, Any] | None,
    ) -> str:
        parts = [
            user_message or "",
            intent or "",
            research_goal or "",
            " ".join(f"{key} {value}" for key, value in (constraints or {}).items()),
        ]
        return " ".join(parts).lower()

    def _score_principle(self, principle: dict[str, Any], text: str, intent: str) -> int:
        keywords = ((principle.get("triggers") or {}).get("keywords") or [])
        score = 0
        for keyword in keywords:
            needle = str(keyword).lower()
            if needle and _keyword_matches(needle, text):
                score += 3 if len(needle) > 2 else 1
        principle_id = str(principle.get("principle_id", ""))
        if score > 0 and intent in {"conceptual_explanation", "research_consultation", "experiment_planning"}:
            score += 1
        if "cfd" in text or "freeflow" in text:
            if principle_id == "cfd_result_schema_first":
                score += 5
        if "re=" in text or "雷诺数" in text or "低雷诺" in text:
            if principle_id == "low_re_nonreciprocal_actuation":
                score += 5
        if "软体" in text or "soft swimmer" in text or "机器鱼" in text:
            if principle_id == "traveling_wave_swimmer_parameterization":
                score += 5
        if "闭环" in text or "control" in text:
            if principle_id == "active_flow_control_formulation":
                score += 4
        return score

    def _to_selected(self, principle: dict[str, Any]) -> SelectedPrinciple:
        return SelectedPrinciple(
            principle_id=str(principle.get("principle_id", "")),
            domain=str(principle.get("domain", "")),
            internal_guidance=[str(item) for item in principle.get("internal_guidance", [])],
            design_implications=[str(item) for item in principle.get("design_implications", [])],
            forbidden_user_facing_phrases=[
                str(item) for item in principle.get("forbidden_user_facing_phrases", [])
            ],
            source_ids=[str(item) for item in principle.get("source_ids", [])],
            confidence=str(principle.get("confidence", "")),
        )


def _keyword_matches(needle: str, text: str) -> bool:
    if needle == "re":
        return bool(re.search(r"(?<![a-z])re\s*=?\s*\d+(?![a-z])|(?<![a-z])re(?![a-z])|雷诺数|低雷诺", text))
    return needle in text
