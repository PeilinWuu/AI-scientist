"""Detect ineffective revision loops before they become infinite retries."""

from __future__ import annotations

import re

from src.ai_scientist.schemas import ResearchProject, RevisionSnapshot


def build_revision_snapshot(project: ResearchProject) -> RevisionSnapshot:
    metrics = project.quality_metrics
    review = project.reviews[-1] if project.reviews else None
    return RevisionSnapshot(
        iteration=project.iteration,
        evidence_coverage=metrics.evidence_coverage,
        verified_source_count=metrics.verified_evidence_count,
        unverifiable_source_count=metrics.unverifiable_source_count,
        primary_source_ratio=metrics.primary_source_ratio,
        unsupported_claim_count=metrics.unsupported_key_claims,
        reviewer_min_score=metrics.reviewer_min_score,
        blocking_issue_signatures=[_signature(item) for item in (review.blocking_issues if review else [])],
        unique_evidence_ids=sorted({item.evidence_id for item in project.evidence if not item.duplicate_of}),
        pending_revision_targets=[
            _normalize_target(item.target)
            for item in project.pending_revision_actions
            if item.status in {"pending", "in_progress"}
        ],
    )


def detect_stagnation(project: ResearchProject) -> bool:
    """Return true when the latest two revision snapshots show no meaningful progress."""

    snapshots = project.revision_snapshots
    if len(snapshots) < 2:
        return False
    previous, current = snapshots[-2], snapshots[-1]
    if not set(previous.pending_revision_targets) & set(current.pending_revision_targets):
        return False
    if _has_material_progress(previous, current):
        return False
    previous_issues = set(previous.blocking_issue_signatures)
    current_issues = set(current.blocking_issue_signatures)
    if not previous_issues or not current_issues:
        return False
    overlap = len(previous_issues & current_issues) / max(len(previous_issues | current_issues), 1)
    return overlap >= 0.6


def _has_material_progress(previous: RevisionSnapshot, current: RevisionSnapshot) -> bool:
    if current.verified_source_count > previous.verified_source_count:
        return True
    if current.unverifiable_source_count < previous.unverifiable_source_count:
        return True
    if current.evidence_coverage >= previous.evidence_coverage + 0.05:
        return True
    if current.primary_source_ratio >= previous.primary_source_ratio + 0.05:
        return True
    if current.unsupported_claim_count < previous.unsupported_claim_count:
        return True
    if current.reviewer_min_score >= previous.reviewer_min_score + 0.5:
        return True
    if len(current.blocking_issue_signatures) < len(previous.blocking_issue_signatures):
        return True
    if set(current.unique_evidence_ids) - set(previous.unique_evidence_ids):
        return True
    return False


def _signature(value: str) -> str:
    text = re.sub(r"\W+", " ", value.lower()).strip()
    return " ".join(text.split()[:12])


def _normalize_target(target: str) -> str:
    return {
        "design": "study_design",
        "analysis": "analysis_plan",
    }.get(target, target)
