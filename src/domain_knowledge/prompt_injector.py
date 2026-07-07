"""Build internal prompt context from selected distilled domain principles."""

from __future__ import annotations

from src.domain_knowledge.principle_selector import SelectedPrinciple


def build_internal_domain_context(
    selected_principles: list[SelectedPrinciple],
    user_requested_references: bool = False,
) -> str:
    """Create an internal-only prompt section for Qwen."""

    if not selected_principles:
        return ""

    lines = [
        "Internal distilled domain principles:",
        "- Treat the following principles as your own scientific judgment.",
        "- Do not expose principle_id or source_id in ordinary user-facing replies.",
        "- Do not say 'knowledge base', 'retrieved', 'according to the literature', or similar.",
        "- Do not say '根据某某论文', '文献表明', '根据知识库', or '检索结果显示'.",
    ]
    if user_requested_references:
        lines.append(
            "- The user explicitly asked for references or evidence; you may mention references carefully."
        )
    else:
        lines.append("- The user did not ask for references; do not cite papers or source names.")

    for index, principle in enumerate(selected_principles, start=1):
        lines.append(f"\nPrinciple {index} domain: {principle.domain}")
        lines.append(f"Confidence: {principle.confidence}")
        for item in principle.internal_guidance:
            lines.append(f"- Internal guidance: {item}")
        for item in principle.design_implications:
            lines.append(f"- Design implication: {item}")
        for item in principle.forbidden_user_facing_phrases:
            lines.append(f"- Suppress user-facing phrase unless references requested: {item}")
    return "\n".join(lines)
