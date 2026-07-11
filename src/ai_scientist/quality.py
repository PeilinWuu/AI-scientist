"""Deterministic research-plan quality metrics and gates."""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

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
    for item in evidence:
        key = evidence_dedupe_key(item)
        duplicate_of = seen.get(key)
        if not duplicate_of:
            seen[key] = item.evidence_id
        level = grade_source(item)
        enriched.append(
            item.model_copy(
                update={
                    "source_level": level,
                    "is_primary_source": level == "A",
                    "verified": level in {"A", "B", "C"} and bool(item.source_url or item.citation),
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
    hypotheses = project.hypotheses
    conclusion = project.conclusion
    review = project.reviews[-1] if project.reviews else None

    supported = [item for item in claims if item.status in {"supported", "partially_supported"}]
    disputed = [item for item in claims if item.status == "disputed" or item.contradicting_evidence_ids]
    unsupported = [item for item in claims if item.status in {"unsupported", "unknown"}]
    primary_count = len([item for item in evidence if item.is_primary_source and not item.duplicate_of])
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
        total_key_claims=len(claims),
        supported_key_claims=len(supported),
        disputed_key_claims=len(disputed),
        unsupported_key_claims=len(unsupported),
        evidence_coverage=_ratio(len(supported), len(claims)),
        primary_source_ratio=_ratio(primary_count, len([item for item in evidence if not item.duplicate_of])),
        total_hypotheses=len(hypotheses),
        falsifiable_hypotheses=len([item for item in hypotheses if item.falsification_conditions]),
        hypothesis_completeness=_ratio(len(complete_hypotheses), len(hypotheses)),
        total_conclusions=len(conclusions),
        traceable_conclusions=len(traceable),
        conclusion_traceability=_ratio(len(traceable), len(conclusions)),
        reviewer_min_score=_reviewer_min_score(review),
        blocking_issue_count=len(review.blocking_issues) if review else 0,
        unverifiable_source_count=len([item for item in evidence if item.source_level == "E" or not item.verified]),
    )


def failed_quality_gates(metrics: ResearchQualityMetrics, review: ReviewResult | None) -> list[str]:
    failed: list[str] = []
    if metrics.evidence_coverage < 0.8:
        failed.append("evidence_coverage_below_0.8")
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
    decision = "reject" if "unverifiable_sources_present" in failed else decision_map.get(target, "revise_evidence")
    blocking = list(dict.fromkeys(review.blocking_issues + [f"Quality gate failed: {item}" for item in failed]))
    return review.model_copy(
        update={
            "decision": decision,
            "failed_quality_gates": failed,
            "required_revision_target": target,
            "blocking_issues": blocking,
        }
    )


def required_revision_target(failed: list[str], review: ReviewResult | None = None) -> str:
    if "unverifiable_sources_present" in failed or "evidence_coverage_below_0.8" in failed:
        return "evidence"
    if "hypothesis_completeness_below_0.8" in failed:
        return "hypothesis"
    if review and review.methodological_validity_score < 6:
        return "method"
    if review and review.feasibility_score < 6:
        return "design"
    if "conclusion_traceability_below_0.9" in failed:
        return "analysis"
    return "evidence"


def grade_source(item: EvidenceItem) -> SourceLevel:
    text = " ".join([item.source_type, item.title, item.citation or "", item.source_url or ""]).lower()
    if any(token in text for token in ["doi", "journal", "paper", "article", "dataset", "standard", "official data"]):
        return "A"
    if any(token in text for token in ["systematic review", "meta-analysis", "authority report", "government report"]):
        return "B"
    if any(token in text for token in ["technical documentation", "official", "documentation", "white paper"]):
        return "C"
    if any(token in text for token in ["news", "blog", "press", "media"]):
        return "D"
    if item.source_url or item.citation:
        return "D"
    return "E"


def evidence_dedupe_key(item: EvidenceItem) -> str:
    doi = _doi(item.citation or "") or _doi(item.source_url or "")
    if doi:
        return f"doi:{doi}"
    if item.source_url:
        parsed = urlparse(item.source_url.lower())
        return f"url:{parsed.netloc}{parsed.path}".rstrip("/")
    title = re.sub(r"\W+", " ", item.title.lower()).strip()
    year = re.search(r"(19|20)\d{2}", item.publication_date or item.citation or "")
    return f"title:{title}:year:{year.group(0) if year else ''}"


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


def _text_relevance_score(value: str) -> float:
    return {"high": 1.0, "medium": 0.65, "low": 0.3}.get(value.lower(), 0.0)


def _doi(text: str) -> str | None:
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, flags=re.IGNORECASE)
    return match.group(0).lower() if match else None
