"""Event construction helpers."""

from __future__ import annotations

from src.ai_scientist.schemas import ResearchEvent, ResearchPhase, utc_now


def completed_event(
    project_id: str,
    phase: ResearchPhase,
    agent_name: str,
    status: str = "completed",
    **kwargs: object,
) -> ResearchEvent:
    """Create a completed, schema-valid event without hidden content."""

    return ResearchEvent(
        project_id=project_id,
        phase=phase,
        agent_name=agent_name,
        status=status,
        finished_at=utc_now(),
        **kwargs,
    )
