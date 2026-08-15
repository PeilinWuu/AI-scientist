"""Deterministic research-plan quality metrics and gates."""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

from src.ai_scientist.evidence_verifier import evidence_dedupe_key, verify_evidence_item
from src.ai_scientist.schemas import (
    Claim,
    Conclusion,
    EvidenceItem,
    Hypothesis,
    ResearchProject,
    ResearchQualityMetrics,
    ReviewResult,
    SourceLevel,
)


def enrich_evidence_items(evidence: list[EvidenceItem]) -> list[EvidenceItem]:
    """Grade and deduplicate evidence without inventing source facts."""

    seen: dict[str, str] = {}
    enriched: list[EvidenceItem] = []
    for raw_item in evidence:
        item = verify_evidence_item(raw_item)
        key = evidence_dedupe_key(item)
        duplicate_of = seen.get(key)
        if not duplicate_of:
            seen[key] = item.evidence_id
        level = grade_source(item)
        is_primary = _is_primary_source(item, level)
        enriched.append(
            item.model_copy(
                update={
                    "source_level": level,
                    "is_primary_source": is_primary,
                    "verified": item.verification_status == "verified",
                    "duplicate_of": duplicate_of,
                    "reliability_score": max(item.reliability_score, _level_reliability(level)),
                    "relevance_score": max(item.relevance_score, _text_relevance_score(item.relevance)),
                }
            )
        )
    return enriched


def compute_quality_metrics(project: ResearchProject) -> ResearchQualityMetrics:
    """Compute V0.2 quality metrics with zero-safe division."""

    claims = _unique_claims(project.claims)
    evidence = project.evidence
    unique_evidence = [item for item in evidence if not item.duplicate_of]
    verified_evidence = [item for item in unique_evidence if item.verification_status == "verified"]
    hypotheses = project.hypotheses
    conclusion = project.conclusion
    review = project.reviews[-1] if project.reviews else None

    supported = [item for item in claims if item.status in {"supported", "partially_supported"}]
    disputed = [item for item in claims if item.status == "disputed" or item.contradicting_evidence_ids]
    unsupported = [item for item in claims if item.status in {"unsupported", "unknown"}]
    primary_count = len([item for item in verified_evidence if item.is_primary_source])
    complete_hypotheses = [
        item
        for item in hypotheses
        if item.predictions and item.falsification_conditions and item.alternative_explanations
    ]
    conclusions = conclusion.supported_findings if conclusion else []
    traceable = [
        item
        for item in conclusions
        if item.supporting_claim_ids and all(_claim_has_supported_evidence(claim_id, claims) for claim_id in item.supporting_claim_ids)
    ]
    return ResearchQualityMetrics(
        total_evidence_count=len(evidence),
        verified_evidence_count=len(verified_evidence),
        partially_verified_evidence_count=len(
            [item for item in unique_evidence if item.verification_status == "partially_verified"]
        ),
        unverified_evidence_count=len(
            [item for item in unique_evidence if item.verification_status in {"unverified", "invalid", "contradicted"}]
        ),
        unique_evidence_count=len(unique_evidence),
        verified_primary_source_count=primary_count,
        evidence_verification_rate=_ratio(len(verified_evidence), len(unique_evidence)),
        total_key_claims=len(claims),
        supported_key_claims=len(supported),
        disputed_key_claims=len(disputed),
        unsupported_key_claims=len(unsupported),
        evidence_coverage=_ratio(len(supported), len(claims)),
        primary_source_ratio=_ratio(primary_count, len(unique_evidence)),
        total_hypotheses=len(hypotheses),
        falsifiable_hypotheses=len([item for item in hypotheses if item.falsification_conditions]),
        hypothesis_completeness=_ratio(len(complete_hypotheses), len(hypotheses)),
        total_conclusions=len(conclusions),
        traceable_conclusions=len(traceable),
        conclusion_traceability=_ratio(len(traceable), len(conclusions)),
        reviewer_min_score=_reviewer_min_score(review),
        blocking_issue_count=len(review.blocking_issues) if review else 0,
        unverifiable_source_count=len(
            [item for item in unique_evidence if item.verification_status not in {"verified", "partially_verified"}]
        ),
    )


def failed_quality_gates(metrics: ResearchQualityMetrics, review: ReviewResult | None) -> list[str]:
    failed: list[str] = []
    if metrics.evidence_coverage < 0.8 and (
        metrics.unique_evidence_count == 0 or metrics.evidence_verification_rate < 0.5
    ):
        failed.append("evidence_coverage_below_0.8")
    if metrics.primary_source_ratio < 0.2 and metrics.total_key_claims and metrics.verified_evidence_count == 0:
        failed.append("primary_source_ratio_below_0.2")
    if metrics.hypothesis_completeness < 0.8:
        failed.append("hypothesis_completeness_below_0.8")
    if metrics.total_conclusions and metrics.conclusion_traceability < 0.9:
        failed.append("conclusion_traceability_below_0.9")
    if metrics.unverifiable_source_count > 0:
        failed.append("unverifiable_sources_present")
    if review:
        if _reviewer_min_score(review) < 6:
            failed.append("reviewer_score_below_6")
        if review.blocking_issues:
            failed.append("blocking_issues_present")
    return failed


def apply_reviewer_quality_gates(review: ReviewResult, metrics: ResearchQualityMetrics) -> ReviewResult:
    """Force reviewer output to respect deterministic gates."""

    failed = failed_quality_gates(metrics, review)
    if not failed:
        return review
    target = required_revision_target(failed, review)
    decision_map = {
        "evidence": "revise_evidence",
        "hypothesis": "revise_hypothesis",
        "method": "revise_method",
        "design": "revise_design",
        "analysis": "revise_analysis",
        "question": "revise_question",
    }
    decision = "reject" if review.decision == "reject" else decision_map.get(target, "revise_evidence")
    # Failed gates are system metadata, not scientific revision issues.
    blocking = list(dict.fromkeys(review.blocking_issues))
    revision_plan = _merge_revision_actions(review.revision_plan, failed, target, blocking)
    return review.model_copy(
        update={
            "decision": decision,
            "failed_quality_gates": failed,
            "required_revision_target": target,
            "blocking_issues": blocking,
            "revision_plan": revision_plan,
        }
    )


def required_revision_target(failed: list[str], review: ReviewResult | None = None) -> str:
    if (
        "unverifiable_sources_present" in failed
        or "evidence_coverage_below_0.8" in failed
        or "primary_source_ratio_below_0.2" in failed
    ):
        return "evidence"
    if "hypothesis_completeness_below_0.8" in failed:
        return "hypothesis"
    if review and review.required_revision_target != "none":
        return review.required_revision_target
    if review and review.methodological_validity_score < 6:
        return "method"
    if review and review.feasibility_score < 6:
        return "design"
    if "conclusion_traceability_below_0.9" in failed:
        return "analysis"
    return "evidence"


def _merge_revision_actions(existing: list, failed: list[str], target: str, blocking: list[str]) -> list:
    from src.ai_scientist.schemas import RevisionAction

    actions = list(existing)
    targets = {_normalize_revision_target(item.target) for item in actions}
    required_targets: list[str] = []
    if target != "none":
        required_targets.append(target)
    if any(item in failed for item in ["reviewer_score_below_6", "blocking_issues_present"]):
        text = " ".join(blocking).lower()
        if any(token in text for token in ["protocol", "reproducib", "boolean", "database", "screening"]):
            required_targets.append("reproducibility_plan")
        if any(token in text for token in ["analysis", "conflate", "clinical", "mechanism", "structure"]):
            required_targets.append("analysis_plan")
    priority = len(actions) + 1
    for item in required_targets:
        normalized = _normalize_revision_target(item)
        if normalized in targets:
            continue
        actions.append(
            RevisionAction(
                target=normalized,
                priority=priority,
                reason=f"Quality review requires revision of {normalized}.",
                required_changes=[issue for issue in blocking[:5]],
                completion_criteria=[f"{normalized} revision is completed and re-reviewed."],
            )
        )
        priority += 1
        targets.add(normalized)
    return sorted(actions, key=lambda item: item.priority)


def _normalize_revision_target(target: str) -> str:
    return {
        "design": "study_design",
        "analysis": "analysis_plan",
        "method": "method",
        "hypothesis": "hypothesis",
    }.get(target, target)


def grade_source(item: EvidenceItem) -> SourceLevel:
    """Deterministically grade source level from verification, not model preference."""

    status = item.verification_status
    has_formal_id = bool(item.doi or item.pmid or item.arxiv_id or item.official_record_url)
    text = " ".join(
        [
            item.source_type,
            item.title,
            item.citation or "",
            item.source_url or "",
            item.journal_or_publisher or "",
        ]
    ).lower()
    if status == "invalid" or status == "contradicted":
        return "E"
    if status != "verified":
        return "D" if item.source_url or item.authors or item.publication_year else "E"
    if _is_primary_source(item, "A") and has_formal_id:
        return "A"
    if any(token in text for token in ["systematic review", "meta-analysis", "authority report", "government report", "guideline"]):
        return "B"
    if any(token in text for token in ["technical documentation", "official", "documentation", "white paper", "method"]):
        return "C"
    if any(token in text for token in ["news", "blog", "press", "media"]):
        return "D"
    return "C" if has_formal_id or item.source_url else "D"


def source_level_distribution(evidence: list[EvidenceItem]) -> dict[str, int]:
    counts = Counter(item.source_level for item in evidence)
    return {level: counts.get(level, 0) for level in ["A", "B", "C", "D", "E"]}


def _unique_claims(claims: list[Claim]) -> list[Claim]:
    seen: set[str] = set()
    unique: list[Claim] = []
    for claim in claims:
        key = claim.statement.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(claim)
    return unique


def _claim_has_supported_evidence(claim_id: str, claims: list[Claim]) -> bool:
    for claim in claims:
        if claim.claim_id == claim_id:
            return claim.status in {"supported", "partially_supported"} and bool(claim.supporting_evidence_ids)
    return False


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _reviewer_min_score(review: ReviewResult | None) -> float:
    if not review:
        return 0.0
    return min(
        review.evidence_quality_score,
        review.methodological_validity_score,
        review.feasibility_score,
        review.reproducibility_score,
        review.claim_support_score,
        review.uncertainty_handling_score,
    )


def _level_reliability(level: SourceLevel) -> float:
    return {"A": 1.0, "B": 0.85, "C": 0.7, "D": 0.45, "E": 0.0}[level]


def _is_primary_source(item: EvidenceItem, level: SourceLevel) -> bool:
    if item.verification_status != "verified":
        return False
    text = " ".join([item.source_type, item.title, item.journal_or_publisher or ""]).lower()
    if any(token in text for token in ["review", "meta-analysis", "guideline", "report", "blog", "news"]):
        return False
    if item.source_type in {"paper", "article", "dataset"} and level == "A":
        return True
    return bool(item.is_primary_source and level == "A")


def _text_relevance_score(value: str) -> float:
    return {"high": 1.0, "medium": 0.65, "low": 0.3}.get(value.lower(), 0.0)


def _doi(text: str) -> str | None:
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, flags=re.IGNORECASE)
    return match.group(0).lower() if match else None
