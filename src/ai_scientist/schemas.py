"""Domain-neutral structured objects used by the AI Scientist workflow."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import os
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResearchPhase(str, Enum):
    INTAKE = "INTAKE"
    QUESTION_FORMULATION = "QUESTION_FORMULATION"
    RESEARCH_MODE_SELECTION = "RESEARCH_MODE_SELECTION"
    DOMAIN_SELECTION = "DOMAIN_SELECTION"
    BACKGROUND_RESEARCH = "BACKGROUND_RESEARCH"
    SEARCH_PLAN_REVIEW = "SEARCH_PLAN_REVIEW"
    HUMAN_SOURCE_REVIEW = "HUMAN_SOURCE_REVIEW"
    CLAIM_EVIDENCE_MAPPING = "CLAIM_EVIDENCE_MAPPING"
    HYPOTHESIS_GENERATION = "HYPOTHESIS_GENERATION"
    METHOD_SELECTION = "METHOD_SELECTION"
    STUDY_DESIGN = "STUDY_DESIGN"
    ANALYSIS_PLANNING = "ANALYSIS_PLANNING"
    FEASIBILITY_REVIEW = "FEASIBILITY_REVIEW"
    HUMAN_REVISION_REVIEW = "HUMAN_REVISION_REVIEW"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    HUMAN_INTERVENTION_REQUIRED = "HUMAN_INTERVENTION_REQUIRED"
    EXECUTION_WAITING = "EXECUTION_WAITING"
    EXECUTION = "EXECUTION"
    DATA_ANALYSIS = "DATA_ANALYSIS"
    CRITICAL_REVIEW = "CRITICAL_REVIEW"
    REVISION = "REVISION"
    SYNTHESIS = "SYNTHESIS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ResearchMode(str, Enum):
    THEORETICAL = "theoretical"
    CONTROLLED_EXPERIMENT = "controlled_experiment"
    OBSERVATIONAL = "observational"
    COMPUTATIONAL_EXPERIMENT = "computational_experiment"
    SIMULATION = "simulation"
    DATA_ANALYSIS = "data_analysis"
    SYSTEMATIC_REVIEW = "systematic_review"
    ENGINEERING_DESIGN = "engineering_design"
    MIXED_METHODS = "mixed_methods"


class ResearchQuestion(StrictModel):
    original_question: str
    normalized_question: str
    question_type: str = ""
    objective: str = ""
    scope: str = ""
    operational_definitions: list[str] = Field(default_factory=list)
    measurable_success_criteria: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    knowns: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


SourceLevel = Literal["A", "B", "C", "D", "E"]
VerificationStatus = Literal["verified", "partially_verified", "unverified", "contradicted", "invalid"]
VerificationMethod = Literal[
    "doi",
    "pmid",
    "arxiv",
    "official_url",
    "publisher_record",
    "exact_title_match",
    "title_author_year_match",
    "none",
]
EvidenceReviewMode = Literal["AUTO", "ASSISTED", "MANUAL"]
ClaimDimension = Literal[
    "structural_existence",
    "physiological_function",
    "biophysical_mechanism",
    "clinical_efficacy",
    "theoretical_concept",
    "safety",
    "unspecified",
]


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, dict):
                text = item.get("title") or item.get("url") or item.get("name") or item.get("text")
                if text:
                    result.append(str(text))
            else:
                text = str(item).strip()
                if text:
                    result.append(text)
        return result
    return [str(value)]


def normalize_evidence_payload(data: Any) -> Any:
    """Normalize a parsed evidence dict without guessing JSON from text."""

    if not isinstance(data, dict):
        return data
    normalized = dict(data)
    normalized.pop("supporting_sources", None)
    if not normalized.get("evidence_id"):
        normalized.pop("evidence_id", None)
    normalized["authors"] = _string_list(normalized.get("authors"))
    normalized["limitations"] = _string_list(normalized.get("limitations"))
    normalized["extracted_claims"] = _string_list(normalized.get("extracted_claims"))
    if not normalized.get("source_url"):
        normalized["source_url"] = None
        normalized["verified"] = False
        normalized.setdefault("verification_note", "No verifiable URL was returned.")
    if not normalized.get("doi"):
        normalized["doi"] = None
    if not normalized.get("pmid"):
        normalized["pmid"] = None
    if not normalized.get("arxiv_id"):
        normalized["arxiv_id"] = None
    if not normalized.get("official_record_url"):
        normalized["official_record_url"] = None
    if not normalized.get("journal_or_publisher"):
        normalized["journal_or_publisher"] = None
    if normalized.get("publication_year") is None and normalized.get("publication_date"):
        year_match = str(normalized.get("publication_date"))
        normalized["publication_year"] = year_match[:4] if year_match[:4].isdigit() else None
    if normalized.get("publication_date") is not None:
        normalized["publication_date"] = str(normalized["publication_date"])
    if not normalized.get("verification_status"):
        normalized["verification_status"] = "unverified"
    if not normalized.get("verification_method"):
        normalized["verification_method"] = "none"
    source_type = str(normalized.get("source_type") or "unknown").strip().lower().replace(" ", "_")
    allowed = {"paper", "article", "website", "report", "review", "dataset", "book", "unknown", "web_source"}
    normalized["source_type"] = source_type if source_type in allowed else "unknown"
    for key in ("reliability", "relevance"):
        if normalized.get(key) is None:
            normalized[key] = "unknown"
        elif not isinstance(normalized.get(key), str):
            normalized[key] = str(normalized[key])
    return normalized


class EvidenceItem(StrictModel):
    evidence_id: str = Field(default_factory=lambda: new_id("evidence"))
    title: str
    source_type: str = "unknown"
    source_url: str | None = None
    citation: str | None = None
    authors: list[str] = Field(default_factory=list)
    doi: str | None = None
    pmid: str | None = None
    arxiv_id: str | None = None
    official_record_url: str | None = None
    journal_or_publisher: str | None = None
    summary: str
    extracted_claims: list[str] = Field(default_factory=list)
    reliability: str = "unknown"
    relevance: str = "unknown"
    limitations: list[str] = Field(default_factory=list)
    publication_date: str | None = None
    publication_year: str | None = None
    status: str = "unverified"
    source_level: SourceLevel = "E"
    is_primary_source: bool = False
    verified: bool = False
    verification_status: VerificationStatus = "unverified"
    verification_method: VerificationMethod = "none"
    verification_note: str | None = None
    duplicate_of: str | None = None
    retrieval_date: datetime = Field(default_factory=utc_now)
    reliability_score: float = Field(default=0.0, ge=0.0, le=1.0)
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    selection_provenance: "SelectionProvenance | None" = None

    @model_validator(mode="before")
    @classmethod
    def normalize_evidence_payload(cls, data: Any) -> Any:
        """Normalize common model variants before strict validation."""

        return normalize_evidence_payload(data)


class SearchSource(StrictModel):
    title: str = ""
    url: str | None = None
    site_name: str | None = None
    snippet: str | None = None
    publication_date: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_source(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"title": data, "url": data if data.startswith(("http://", "https://")) else None}
        if not isinstance(data, dict):
            return {"title": str(data)}
        normalized = dict(data)
        normalized["title"] = str(normalized.get("title") or normalized.get("name") or normalized.get("url") or "")
        normalized["url"] = normalized.get("url") or normalized.get("source_url") or normalized.get("link")
        if not normalized.get("url"):
            normalized["url"] = None
        normalized["site_name"] = normalized.get("site_name") or normalized.get("site") or normalized.get("domain")
        normalized["snippet"] = normalized.get("snippet") or normalized.get("summary") or normalized.get("text")
        if normalized.get("publication_date") is not None:
            normalized["publication_date"] = str(normalized["publication_date"])
        return {
            "title": normalized.get("title") or "",
            "url": normalized.get("url"),
            "site_name": normalized.get("site_name"),
            "snippet": normalized.get("snippet"),
            "publication_date": normalized.get("publication_date"),
        }


class SearchPlan(StrictModel):
    """Bounded, offline plan for discovering relevant sources."""

    search_plan_id: str = Field(default_factory=lambda: new_id("search_plan"))
    project_id: str = ""
    research_question_version: int = 1
    question_hash: str = ""
    version: int = Field(default=1, ge=1)
    generated_at: datetime = Field(default_factory=utc_now)
    planner_model: str = ""
    queries: list[str] = Field(default_factory=list)
    target_source_types: list[str] = Field(default_factory=list)
    preferred_databases: list[str] = Field(default_factory=list)
    date_constraints: list[str] = Field(default_factory=list)
    maximum_queries: int = Field(default=4, ge=1)
    rationale: str = ""
    relevance_status: Literal["pending", "relevant", "partially_relevant", "irrelevant"] = "pending"
    relevance_note: str = ""
    approved_at: datetime | None = None
    approved_by: Literal["human", "system"] | None = None

    @model_validator(mode="after")
    def bound_queries(self) -> "SearchPlan":
        configured = max(1, int(os.getenv("AI_SCIENTIST_MAX_SEARCH_QUERIES", "4")))
        maximum = min(self.maximum_queries, configured)
        unique = list(dict.fromkeys(item.strip() for item in self.queries if item.strip()))[:maximum]
        self.maximum_queries = maximum
        self.queries = unique
        return self


class SourceCandidate(StrictModel):
    """One source discovered by one bounded web-search query."""

    candidate_id: str = Field(default_factory=lambda: new_id("candidate"))
    title: str = ""
    url: str = ""
    query: str = ""
    rank: int = Field(default=1, ge=1)
    source_domain: str = ""
    authors: list[str] = Field(default_factory=list)
    publication_year: str | None = None
    journal_or_publisher: str | None = None
    source_type: str = "unknown"
    snippet: str = ""
    doi: str | None = None
    pmid: str | None = None
    arxiv_id: str | None = None
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    verification_status: VerificationStatus = "unverified"
    verification_note: str = ""
    is_primary_source: bool = False
    ai_summary: str = ""
    ai_recommendation: Literal["keep", "reject", "uncertain"] = "uncertain"
    recommendation_reason: str = ""
    human_provided: bool = False
    discovered_at: datetime = Field(default_factory=utc_now)
    selection_score: int = 0
    extraction_status: Literal["pending", "completed", "timeout", "failed", "skipped"] = "pending"
    extracted_text: str = ""
    extraction_error: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_candidate(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["query"] = normalized.get("query") or normalized.get("search_query") or ""
        normalized["rank"] = normalized.get("rank") or normalized.get("search_rank") or 1
        normalized["source_domain"] = normalized.get("source_domain") or normalized.get("domain") or ""
        normalized["authors"] = _string_list(normalized.get("authors"))
        return normalized


# Backward-compatible import name used by the bounded search implementation.
SearchCandidate = SourceCandidate


class CuratedSource(StrictModel):
    candidate_id: str
    decision: Literal["keep", "reject", "defer"]
    decided_by: Literal["human", "system"] = "human"
    human_note: str = ""
    rejection_reason: str = ""
    decided_at: datetime = Field(default_factory=utc_now)


class SelectionProvenance(StrictModel):
    selected_by: Literal["human", "system"]
    selection_id: str
    candidate_id: str
    verification_method: str = "none"


class SourceCandidateCollection(StrictModel):
    collection_id: str = Field(default_factory=lambda: new_id("candidate_collection"))
    project_id: str
    question_hash: str
    search_plan_id: str
    candidates: list[SourceCandidate] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class SourceSelectionSnapshot(StrictModel):
    selection_id: str = Field(default_factory=lambda: new_id("source_selection"))
    project_id: str
    iteration: int = 0
    research_question_version: int = 1
    search_plan_version: int = 1
    kept_candidate_ids: list[str] = Field(default_factory=list)
    rejected_candidate_ids: list[str] = Field(default_factory=list)
    deferred_candidate_ids: list[str] = Field(default_factory=list)
    human_added_source_ids: list[str] = Field(default_factory=list)
    decisions: list[CuratedSource] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    created_by: Literal["human", "system"] = "human"
    selection_note: str = ""


class SourceReviewFeedbackSummary(StrictModel):
    rejection_reason_counts: dict[str, int] = Field(default_factory=dict)
    concise_feedback: list[str] = Field(default_factory=list)


class ResearchAsset(StrictModel):
    asset_id: str = Field(default_factory=lambda: new_id("asset"))
    filename: str
    content_type: str = "application/octet-stream"
    saved_path: str
    size_bytes: int = Field(default=0, ge=0)
    parsing_status: Literal["registered_only", "parsed"] = "registered_only"
    created_at: datetime = Field(default_factory=utc_now)


class SearchPlanRelevanceValidation(StrictModel):
    status: Literal["relevant", "partially_relevant", "irrelevant"]
    reason: str = ""


class SearchQueryRecord(StrictModel):
    query: str
    status: Literal["pending", "completed", "timeout", "failed"] = "pending"
    candidate_count: int = 0
    requested_model: str = ""
    actual_model: str = ""
    fallback_reason: str = ""
    original_error: str = ""
    elapsed_seconds: float = Field(default=0.0, ge=0.0)


class SearchAcquisitionResult(StrictModel):
    final_text: str = ""
    sources: list[SearchSource] = Field(default_factory=list)
    response_id: str | None = None
    request_id: str | None = None
    search_used: bool = False
    warnings: list[str] = Field(default_factory=list)
    search_plan: SearchPlan | None = None
    query_records: list[SearchQueryRecord] = Field(default_factory=list)
    candidates: list[SearchCandidate] = Field(default_factory=list)
    selected_candidates: list[SearchCandidate] = Field(default_factory=list)
    usable_source_count: int = 0
    requested_model: str = ""
    actual_model: str = ""
    fallback_reason: str = ""


class EvidenceCollection(StrictModel):
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    preliminary_claims: list["Claim"] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    conflicting_evidence: list[str] = Field(default_factory=list)
    source_summary: str = ""

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_evidence_collection(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if "evidence_items" not in normalized:
            normalized["evidence_items"] = (
                normalized.pop("evidence", None)
                or normalized.pop("normalized_evidence", None)
                or normalized.pop("sources", None)
                or []
            )
        if "preliminary_claims" not in normalized:
            normalized["preliminary_claims"] = normalized.pop("claims", None) or normalized.pop("key_claims", None) or []
        normalized["evidence_gaps"] = _string_list(normalized.get("evidence_gaps"))
        normalized["conflicting_evidence"] = _string_list(normalized.get("conflicting_evidence"))
        if normalized.get("source_summary") is None:
            normalized["source_summary"] = ""
        return normalized


class DomainResolution(StrictModel):
    reported_primary_domain: str
    reported_secondary_domains: list[str] = Field(default_factory=list)
    canonical_primary_domain: str
    canonical_secondary_domains: list[str] = Field(default_factory=list)
    loaded_domain_skill: str
    fallback_used: bool = False
    mapping_reason: str = ""


ClaimType = Literal["observation", "reported_fact", "inference", "hypothesis", "prediction", "conclusion"]
ClaimStatus = Literal["supported", "partially_supported", "disputed", "unsupported", "unknown"]


class Claim(StrictModel):
    claim_id: str = Field(default_factory=lambda: new_id("claim"))
    statement: str
    claim_type: ClaimType
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    status: ClaimStatus = "unknown"
    dimension: ClaimDimension = "unspecified"

    @model_validator(mode="before")
    @classmethod
    def normalize_claim_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if not normalized.get("claim_id"):
            normalized.pop("claim_id", None)
        normalized.pop("supporting_sources", None)
        normalized["supporting_evidence_ids"] = _string_list(normalized.get("supporting_evidence_ids"))
        normalized["contradicting_evidence_ids"] = _string_list(normalized.get("contradicting_evidence_ids"))
        normalized["assumptions"] = _string_list(normalized.get("assumptions"))
        normalized["limitations"] = _string_list(normalized.get("limitations"))
        normalized["claim_type"] = normalized.get("claim_type") or "reported_fact"
        normalized["status"] = normalized.get("status") or "unknown"
        normalized["dimension"] = normalized.get("dimension") or _infer_claim_dimension(str(normalized.get("statement") or ""))
        return normalized


class ClaimEvidenceLink(StrictModel):
    claim_id: str
    evidence_id: str
    relation: Literal["supports", "contradicts", "contextualizes", "insufficient"]
    strength: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str

    @model_validator(mode="before")
    @classmethod
    def normalize_link_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["claim_id"] = str(
            normalized.get("claim_id")
            or normalized.get("source_claim_id")
            or normalized.get("claim")
            or ""
        ).strip()
        normalized["evidence_id"] = str(
            normalized.get("evidence_id")
            or normalized.get("source_evidence_id")
            or normalized.get("evidence")
            or ""
        ).strip()
        relation = str(normalized.get("relation") or normalized.get("relationship") or "contextualizes").lower()
        if relation in {"support", "supported_by", "supports_claim"}:
            relation = "supports"
        elif relation in {"contradict", "contradiction", "refutes", "against"}:
            relation = "contradicts"
        elif relation in {"unclear", "weak", "none", "missing"}:
            relation = "insufficient"
        normalized["relation"] = relation
        normalized["rationale"] = str(normalized.get("rationale") or normalized.get("reason") or "").strip()
        return {
            "claim_id": normalized["claim_id"],
            "evidence_id": normalized["evidence_id"],
            "relation": normalized["relation"],
            "strength": normalized.get("strength"),
            "rationale": normalized["rationale"],
        }


class ClaimItem(StrictModel):
    claim_id: str = ""
    statement: str
    claim_type: str
    importance: str
    status: str
    dimension: ClaimDimension = "unspecified"
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_claim_item_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["claim_id"] = str(normalized.get("claim_id") or normalized.get("id") or "").strip()
        normalized["claim_type"] = str(normalized.get("claim_type") or "reported_fact")
        normalized["importance"] = str(normalized.get("importance") or "medium")
        normalized["status"] = str(normalized.get("status") or "unknown")
        normalized["dimension"] = normalized.get("dimension") or _infer_claim_dimension(str(normalized.get("statement") or ""))
        normalized["supporting_evidence_ids"] = _string_list(normalized.get("supporting_evidence_ids"))
        normalized["contradicting_evidence_ids"] = _string_list(normalized.get("contradicting_evidence_ids"))
        normalized["assumptions"] = _string_list(normalized.get("assumptions"))
        normalized["limitations"] = _string_list(normalized.get("limitations"))
        return {
            "claim_id": normalized["claim_id"],
            "statement": normalized.get("statement"),
            "claim_type": normalized["claim_type"],
            "importance": normalized["importance"],
            "status": normalized["status"],
            "dimension": normalized["dimension"],
            "supporting_evidence_ids": normalized["supporting_evidence_ids"],
            "contradicting_evidence_ids": normalized["contradicting_evidence_ids"],
            "assumptions": normalized["assumptions"],
            "limitations": normalized["limitations"],
        }

    @field_validator("claim_type")
    @classmethod
    def normalize_claim_type(cls, value: str) -> str:
        allowed = {"observation", "reported_fact", "inference", "hypothesis", "prediction", "conclusion"}
        return value if value in allowed else "reported_fact"

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        allowed = {"supported", "partially_supported", "disputed", "unsupported", "unknown"}
        return value if value in allowed else "unknown"


class ClaimEvidenceMappingResult(StrictModel):
    claims: list[ClaimItem] = Field(default_factory=list)
    links: list[ClaimEvidenceLink] = Field(default_factory=list)
    unsupported_claim_ids: list[str] = Field(default_factory=list)
    disputed_claim_ids: list[str] = Field(default_factory=list)
    evidence_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    display_markdown: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_mapping_result_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        normalized["claims"] = normalized.get("claims") or normalized.pop("claim_items", None) or []
        normalized["links"] = (
            normalized.get("links")
            or normalized.pop("claim_evidence_links", None)
            or normalized.pop("mappings", None)
            or normalized.pop("claim_mappings", None)
            or []
        )
        normalized["unsupported_claim_ids"] = _string_list(normalized.get("unsupported_claim_ids"))
        normalized["disputed_claim_ids"] = _string_list(normalized.get("disputed_claim_ids"))
        if normalized.get("evidence_coverage") is None:
            normalized["evidence_coverage"] = 0.0
        if normalized.get("display_markdown") is None:
            normalized["display_markdown"] = ""
        return normalized


class Hypothesis(StrictModel):
    hypothesis_id: str = Field(default_factory=lambda: new_id("hypothesis"))
    statement: str
    mechanism: str
    predictions: list[str]
    falsification_conditions: list[str]
    alternative_explanations: list[str]
    required_evidence: list[str]
    supporting_claim_ids: list[str] = Field(default_factory=list)
    status: str = "proposed"


class StudyDesign(StrictModel):
    research_mode: ResearchMode
    objective: str
    hypotheses_tested: list[str] = Field(default_factory=list)
    population_or_system: str = ""
    variables: list[str] = Field(default_factory=list)
    controls: list[str] = Field(default_factory=list)
    comparison_groups: list[str] = Field(default_factory=list)
    sampling_plan: list[str] = Field(default_factory=list)
    data_collection_plan: list[str] = Field(default_factory=list)
    measurement_plan: list[str] = Field(default_factory=list)
    analysis_plan: list[str] = Field(default_factory=list)
    quality_controls: list[str] = Field(default_factory=list)
    stopping_rules: list[str] = Field(default_factory=list)
    feasibility: str = "unknown"
    required_tools: list[str] = Field(default_factory=list)
    human_actions_required: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    ethical_considerations: list[str] = Field(default_factory=list)
    reproducibility_plan: list[str] = Field(default_factory=list)


class AnalysisPlan(StrictModel):
    objectives: list[str] = Field(default_factory=list)
    input_data: list[str] = Field(default_factory=list)
    preprocessing: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    statistical_assumptions: list[str] = Field(default_factory=list)
    statistical_methods: list[str] = Field(default_factory=list)
    robustness_checks: list[str] = Field(default_factory=list)
    sensitivity_analysis: list[str] = Field(default_factory=list)
    uncertainty_quantification: list[str] = Field(default_factory=list)
    visualization_plan: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    failure_criteria: list[str] = Field(default_factory=list)


ReviewDecision = Literal[
    "approve",
    "revise_question",
    "revise_evidence",
    "revise_hypothesis",
    "revise_method",
    "revise_design",
    "revise_analysis",
    "reject",
]


class ReviewResult(StrictModel):
    evidence_quality_score: float = Field(ge=0, le=10)
    methodological_validity_score: float = Field(ge=0, le=10)
    feasibility_score: float = Field(ge=0, le=10)
    reproducibility_score: float = Field(ge=0, le=10)
    claim_support_score: float = Field(ge=0, le=10)
    uncertainty_handling_score: float = Field(ge=0, le=10)
    blocking_issues: list[str] = Field(default_factory=list)
    non_blocking_issues: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    decision: ReviewDecision
    failed_quality_gates: list[str] = Field(default_factory=list)
    required_revision_target: Literal[
        "question", "evidence", "hypothesis", "method", "design", "analysis", "none"
    ] = "none"
    revision_plan: list["RevisionAction"] = Field(default_factory=list)
    approval_conditions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_invalid_approval(self) -> "ReviewResult":
        scores = [
            self.evidence_quality_score,
            self.methodological_validity_score,
            self.feasibility_score,
            self.reproducibility_score,
            self.claim_support_score,
            self.uncertainty_handling_score,
        ]
        if self.decision == "approve" and min(scores) < 6:
            raise ValueError("Review decision cannot be approve when any critical score is below 6.")
        if self.decision == "approve" and self.blocking_issues:
            raise ValueError("Review decision cannot be approve with blocking issues.")
        if self.decision == "approve" and self.failed_quality_gates:
            raise ValueError("Review decision cannot be approve with failed quality gates.")
        return self


class ConclusionItem(StrictModel):
    conclusion_id: str = Field(default_factory=lambda: new_id("conclusion"))
    statement: str
    supporting_claim_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    limitations: list[str] = Field(default_factory=list)
    scope_of_validity: list[str] = Field(default_factory=list)
    dimension: ClaimDimension = "unspecified"


class Conclusion(StrictModel):
    planning_status_statement: str = (
        "This report is a research plan produced by AI Scientist. No real experiment, "
        "simulation, or data analysis has been executed, so it must not be treated as an experimental conclusion."
    )
    supported_findings: list[ConclusionItem] = Field(default_factory=list)
    tentative_inferences: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    negative_results: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    scope_of_validity: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    next_questions: list[str] = Field(default_factory=list)
    human_verification_required: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def coerce_legacy_supported_findings(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        findings = data.get("supported_findings")
        if isinstance(findings, list):
            data = dict(data)
            data["supported_findings"] = [
                {"statement": item} if isinstance(item, str) else item
                for item in findings
            ]
        return data


class ResearchQualityMetrics(StrictModel):
    total_evidence_count: int = 0
    verified_evidence_count: int = 0
    partially_verified_evidence_count: int = 0
    unverified_evidence_count: int = 0
    unique_evidence_count: int = 0
    verified_primary_source_count: int = 0
    evidence_verification_rate: float = 0.0
    total_key_claims: int = 0
    supported_key_claims: int = 0
    disputed_key_claims: int = 0
    unsupported_key_claims: int = 0
    evidence_coverage: float = 0.0
    primary_source_ratio: float = 0.0
    total_hypotheses: int = 0
    falsifiable_hypotheses: int = 0
    hypothesis_completeness: float = 0.0
    total_conclusions: int = 0
    traceable_conclusions: int = 0
    conclusion_traceability: float = 0.0
    reviewer_min_score: float = 0.0
    blocking_issue_count: int = 0
    unverifiable_source_count: int = 0
    revision_improvement_score: float = 0.0
    stagnation_detected: bool = False


class RevisionAction(StrictModel):
    target: Literal[
        "question",
        "evidence",
        "hypothesis",
        "method",
        "study_design",
        "design",
        "analysis_plan",
        "analysis",
        "reproducibility_plan",
    ]
    priority: int = Field(default=1, ge=1)
    reason: str
    required_changes: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)
    action_id: str = Field(default_factory=lambda: new_id("revision_action"))
    status: Literal[
        "pending", "in_progress", "completed", "skipped", "needs_attention", "failed_verification"
    ] = "pending"


RevisionIssueClassification = Literal[
    "plan_blocking", "execution_prerequisite", "non_blocking", "optional"
]
RevisionIssueTarget = Literal[
    "question",
    "evidence",
    "hypothesis",
    "methodology",
    "study_design",
    "analysis_plan",
    "reproducibility_plan",
    "execution_requirements",
]
RevisionDisposition = Literal[
    "accept_ai",
    "accept_modified",
    "provide_content",
    "accept_limitation",
    "defer_execution",
    "reject",
]


class RevisionIssue(StrictModel):
    issue_id: str = Field(default_factory=lambda: new_id("revision_issue"))
    source_action_ids: list[str] = Field(default_factory=list)
    classification: RevisionIssueClassification
    target: RevisionIssueTarget
    problem: str
    severity: str = "需要在方案定稿前处理"
    impact: str = "如果不处理，研究计划的可执行性或可复现性可能下降。"
    reviewer_recommendations: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)
    priority: int = Field(default=1, ge=1)
    status: Literal[
        "pending", "approved", "rejected", "deferred", "limitation", "completed", "needs_attention"
    ] = "pending"


class RevisionIssueDecision(StrictModel):
    issue_id: str
    disposition: RevisionDisposition
    instruction: str = ""
    reason: str = ""


class RevisionCriterionResult(StrictModel):
    criterion: str
    passed: bool
    evidence: str = ""
    note: str = ""


class RevisionVerificationResult(StrictModel):
    verification_id: str = Field(default_factory=lambda: new_id("revision_verification"))
    action_id: str
    target_artifact: str
    artifact_version: int
    criteria_results: list[RevisionCriterionResult] = Field(default_factory=list)
    overall_passed: bool = False
    verification_method: str
    verified_at: datetime = Field(default_factory=utc_now)


class RevisionTargetBatch(StrictModel):
    batch_id: str = Field(default_factory=lambda: new_id("revision_batch"))
    target: RevisionIssueTarget
    issue_ids: list[str] = Field(default_factory=list)
    issue_snapshots: list[RevisionIssue] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    provided_content: list[str] = Field(default_factory=list)
    status: Literal["pending", "in_progress", "completed", "needs_attention"] = "pending"
    old_artifact_version: int | None = None
    new_artifact_version: int | None = None
    verification_id: str | None = None
    job_id: str | None = None


class ApprovedRevisionPlan(StrictModel):
    revision_plan_id: str = Field(default_factory=lambda: new_id("approved_revision_plan"))
    project_id: str
    review_version: int
    revision_cycle: int
    approved_issues: list[str] = Field(default_factory=list)
    rejected_issues: list[str] = Field(default_factory=list)
    deferred_issues: list[str] = Field(default_factory=list)
    accepted_as_limitation: list[str] = Field(default_factory=list)
    human_modified_instructions: dict[str, str] = Field(default_factory=dict)
    target_batches: list[RevisionTargetBatch] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    created_by: Literal["human"] = "human"
    status: Literal["approved", "in_progress", "completed", "needs_attention"] = "approved"


class EvidenceRevisionTask(StrictModel):
    task_id: str = Field(default_factory=lambda: new_id("evidence_revision_task"))
    target_claim_ids: list[str] = Field(default_factory=list)
    objective: str
    required_source_types: list[str] = Field(default_factory=list)
    minimum_verified_sources: int = Field(default=1, ge=0)
    search_queries: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)


class RevisionSnapshot(StrictModel):
    iteration: int
    evidence_coverage: float = 0.0
    verified_source_count: int = 0
    unverifiable_source_count: int = 0
    primary_source_ratio: float = 0.0
    unsupported_claim_count: int = 0
    reviewer_min_score: float = 0.0
    blocking_issue_signatures: list[str] = Field(default_factory=list)
    unique_evidence_ids: list[str] = Field(default_factory=list)
    pending_revision_targets: list[str] = Field(default_factory=list)
    stagnation_detected: bool = False


class SystematicReviewProtocol(StrictModel):
    review_question: str = ""
    databases: list[str] = Field(default_factory=list)
    search_date_range: str = ""
    languages: list[str] = Field(default_factory=list)
    boolean_search_strings: list[str] = Field(default_factory=list)
    inclusion_criteria: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)
    screening_process: list[str] = Field(default_factory=list)
    duplicate_screening: str = ""
    conflict_resolution: str = ""
    extraction_fields: list[str] = Field(default_factory=list)
    risk_of_bias_tools: list[str] = Field(default_factory=list)
    evidence_grading_method: str = ""
    synthesis_strategy: str = ""
    subgroup_strategy: str = ""
    translation_protocol: str = ""
    software: list[str] = Field(default_factory=list)
    inter_rater_reliability_metric: str = ""
    inter_rater_reliability_threshold: str = ""
    protocol_registration_plan: str = ""


class BudgetState(StrictModel):
    max_model_calls: int = Field(default=50, ge=1)
    used_model_calls: int = Field(default=0, ge=0)
    attempted_model_calls: int = Field(default=0, ge=0)
    successful_model_calls: int = Field(default=0, ge=0)
    failed_model_calls: int = Field(default=0, ge=0)
    fallback_model_calls: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=2, ge=0)
    used_iterations: int = Field(default=0, ge=0)
    optional_token_budget: int | None = Field(default=None, ge=1)
    optional_cost_budget: float | None = Field(default=None, ge=0)


class ArtifactRecord(StrictModel):
    artifact_id: str
    artifact_type: str
    filename: str
    created_by: str
    created_at: datetime
    checksum: str
    version: int = 1


class ResearchEvent(StrictModel):
    event_id: str = Field(default_factory=lambda: new_id("event"))
    job_id: str | None = None
    project_id: str
    phase: ResearchPhase
    agent_name: str
    requested_model: str | None = None
    actual_model: str | None = None
    fallback_used: bool = False
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    status: str
    error: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    schema_valid: bool = True
    tool_names: list[str] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)
    query_count: int | None = None
    search_result_count: int | None = None
    extracted_page_count: int | None = None
    model_call_count: int | None = None
    evidence_count: int | None = None
    claim_count: int | None = None
    link_count: int | None = None
    invalid_reference_count: int | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    changed_fields: list[str] = Field(default_factory=list)
    feedback: str | None = None
    reason: str | None = None
    previous_phase: ResearchPhase | None = None
    target_phase: ResearchPhase | None = None
    revision_reason: str | None = None
    invalidated_artifact_ids: list[str] = Field(default_factory=list)
    preserved_artifact_ids: list[str] = Field(default_factory=list)
    display_markdown: str = ""
    visibility: Literal["internal", "user"] = "internal"
    display_key: str | None = None
    iteration: int = Field(default=0, ge=0)
    summary_markdown: str = ""
    attempted_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    fallback_calls: int = 0
    failing_component: str | None = None
    failure_category: str | None = None
    artifact_type: str | None = None
    attempted_model: str | None = None
    fallback_attempted: bool = False
    tool_name: str | None = None
    stage_substep: str | None = None
    safe_traceback: str | None = None
    validation_errors: list[dict[str, Any]] = Field(default_factory=list)
    status_code: int | None = None
    provider_error_code: str | None = None
    provider_error_message: str | None = None
    request_id: str | None = None
    endpoint_host: str | None = None
    previous_response_id_present: bool | None = None


class BackgroundResearchCheckpoint(StrictModel):
    project_id: str = ""
    question_hash: str = ""
    search_plan_id: str = ""
    search_artifact_id: str | None = None
    search_completed: bool = False
    normalization_completed: bool = False
    search_payload: dict[str, Any] | None = None
    search_plan: SearchPlan | None = None
    query_records: list[SearchQueryRecord] = Field(default_factory=list)
    candidates: list[SearchCandidate] = Field(default_factory=list)
    selected_candidates: list[SearchCandidate] = Field(default_factory=list)
    source_selection_completed: bool = False
    awaiting_search_plan_review: bool = False
    search_plan_approved: bool = False
    awaiting_source_review: bool = False
    source_selection_id: str | None = None
    extraction_completed: bool = False
    started_at: datetime | None = None
    last_activity_at: datetime | None = None
    elapsed_seconds: float = Field(default=0.0, ge=0.0)


class ReviewPackage(StrictModel):
    """Frozen artifact-version manifest presented for human approval."""

    package_id: str = Field(default_factory=lambda: new_id("review_package"))
    project_id: str
    created_at: datetime = Field(default_factory=utc_now)
    artifact_versions: dict[str, int | None] = Field(default_factory=dict)
    artifact_snapshots: dict[str, Any] = Field(default_factory=dict)
    blocking_issue_count: int = Field(default=0, ge=0)
    reviewer_decision: str = ""
    ready_for_approval: bool = False


class HumanApprovalRecord(StrictModel):
    """Auditable approval tied to the exact versions a reviewer inspected."""

    approval_id: str = Field(default_factory=lambda: new_id("approval"))
    project_id: str
    package_id: str
    approved_at: datetime = Field(default_factory=utc_now)
    approved_versions: dict[str, int | None] = Field(default_factory=dict)
    acknowledgment: bool = False
    status: Literal["valid", "stale"] = "valid"


class HumanRevisionRecord(StrictModel):
    revision_id: str = Field(default_factory=lambda: new_id("human_revision"))
    project_id: str
    target: str
    feedback: str
    requested_at: datetime = Field(default_factory=utc_now)
    artifact_versions: dict[str, int | None] = Field(default_factory=dict)


class ResearchProject(StrictModel):
    project_id: str = Field(default_factory=lambda: new_id("project"))
    title: str
    objective: str
    domain: str = "general"
    secondary_domains: list[str] = Field(default_factory=list)
    domain_resolution: DomainResolution | None = None
    research_mode: ResearchMode | None = None
    secondary_modes: list[ResearchMode] = Field(default_factory=list)
    model_overrides: dict[str, str] = Field(default_factory=dict)
    phase: ResearchPhase = ResearchPhase.INTAKE
    constraints: dict[str, Any] = Field(default_factory=dict)
    question: ResearchQuestion | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    evidence_review_mode: EvidenceReviewMode = "ASSISTED"
    search_plan_history: list[SearchPlan] = Field(default_factory=list)
    source_candidate_collections: list[SourceCandidateCollection] = Field(default_factory=list)
    curated_sources: list[CuratedSource] = Field(default_factory=list)
    source_selection_snapshots: list[SourceSelectionSnapshot] = Field(default_factory=list)
    source_review_feedback: SourceReviewFeedbackSummary = Field(default_factory=SourceReviewFeedbackSummary)
    research_assets: list[ResearchAsset] = Field(default_factory=list)
    auto_approve_search_plan: bool = False
    claims: list[Claim] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    study_design: StudyDesign | None = None
    analysis_plan: AnalysisPlan | None = None
    reproducibility_plan: dict[str, Any] = Field(default_factory=dict)
    reviews: list[ReviewResult] = Field(default_factory=list)
    conclusion: Conclusion | None = None
    quality_metrics: ResearchQualityMetrics = Field(default_factory=ResearchQualityMetrics)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    active_artifact_versions: dict[str, int | None] = Field(default_factory=dict)
    review_package: ReviewPackage | None = None
    human_approval_history: list[HumanApprovalRecord] = Field(default_factory=list)
    human_revision_history: list[HumanRevisionRecord] = Field(default_factory=list)
    revision_issues: list[RevisionIssue] = Field(default_factory=list)
    approved_revision_plans: list[ApprovedRevisionPlan] = Field(default_factory=list)
    revision_verifications: list[RevisionVerificationResult] = Field(default_factory=list)
    active_revision_plan_id: str | None = None
    active_job_id: str | None = None
    execution_requirements: list[str] = Field(default_factory=list)
    accepted_limitations: list[str] = Field(default_factory=list)
    revision_migration_version: int = 0
    revision_recovery_messages: list[str] = Field(default_factory=list)
    approval_valid_for_versions: dict[str, int | None] = Field(default_factory=dict)
    approval_status: Literal["not_requested", "pending", "valid", "stale", "deferred"] = "not_requested"
    version_change_summaries: list[str] = Field(default_factory=list)
    stale_artifacts: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    user_event_keys: list[str] = Field(default_factory=list)
    iteration: int = 0
    max_iterations: int = 2
    budget: BudgetState = Field(default_factory=BudgetState)
    available_tools: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    human_actions_required: list[str] = Field(default_factory=list)
    previous_response_ids: dict[str, str] = Field(default_factory=dict)
    background_research_checkpoint: BackgroundResearchCheckpoint = Field(
        default_factory=BackgroundResearchCheckpoint
    )
    stage_messages: list[str] = Field(default_factory=list)
    planning_only: bool = True
    domain_hint: str | None = None
    method_rationale: str = ""
    validity_threats: list[str] = Field(default_factory=list)
    required_controls: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    conflicting_evidence: list[str] = Field(default_factory=list)
    revision_feedback: list[str] = Field(default_factory=list)
    pending_revision_target: str | None = None
    pending_revision_actions: list[RevisionAction] = Field(default_factory=list)
    completed_revision_actions: list[RevisionAction] = Field(default_factory=list)
    current_revision_action: RevisionAction | None = None
    evidence_revision_tasks: list[EvidenceRevisionTask] = Field(default_factory=list)
    revision_snapshots: list[RevisionSnapshot] = Field(default_factory=list)
    systematic_review_protocol: SystematicReviewProtocol | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DirectorOutput(StrictModel):
    research_question: ResearchQuestion
    project_title: str
    initial_constraints: dict[str, Any] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    initial_risks: list[str] = Field(default_factory=list)


class EvidenceResearchOutput(StrictModel):
    evidence: list[EvidenceItem] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    conflicting_evidence: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    confidence_summary: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_collection_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        if "evidence" not in normalized:
            normalized["evidence"] = (
                normalized.pop("evidence_items", None)
                or normalized.pop("items", None)
                or normalized.pop("sources", None)
                or []
            )
        if "claims" not in normalized:
            normalized["claims"] = normalized.pop("key_claims", None) or normalized.pop("findings", None) or []
        normalized["evidence_gaps"] = _string_list(normalized.get("evidence_gaps"))
        normalized["conflicting_evidence"] = _string_list(normalized.get("conflicting_evidence"))
        normalized["unsupported_claims"] = _string_list(normalized.get("unsupported_claims"))
        if normalized.get("confidence_summary") is None:
            normalized["confidence_summary"] = ""
        return normalized


class MethodSelectionOutput(StrictModel):
    primary_research_mode: ResearchMode
    secondary_modes: list[ResearchMode] = Field(default_factory=list)
    rationale: str
    required_methods: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    unavailable_capabilities: list[str] = Field(default_factory=list)
    human_actions_required: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class DomainSelectionOutput(StrictModel):
    primary_domain: str
    secondary_domains: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    selected_domain_skills: list[str] = Field(default_factory=list)
    clarification_needed: bool = False
    clarification_questions: list[str] = Field(default_factory=list)


class MethodologyOutput(StrictModel):
    selected_research_mode: ResearchMode
    methodological_rationale: str
    validity_threats: list[str] = Field(default_factory=list)
    required_controls: list[str] = Field(default_factory=list)
    required_quality_checks: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    human_actions_required: list[str] = Field(default_factory=list)


class HypothesisOutput(StrictModel):
    hypotheses: list[Hypothesis]
    alternative_explanations: list[str] = Field(default_factory=list)
    discriminating_evidence: list[str] = Field(default_factory=list)
    priority_order: list[str] = Field(default_factory=list)


class ReproducibilityOutput(StrictModel):
    reproducibility_plan: list[str] = Field(default_factory=list)
    required_artifacts: list[str] = Field(default_factory=list)
    missing_reproducibility_information: list[str] = Field(default_factory=list)
    execution_readiness: str


class ResearchStartRequest(StrictModel):
    objective: str
    domain_hint: str | None = None
    constraints_text: str = ""
    constraints: dict[str, Any] = Field(default_factory=dict)
    model_overrides: dict[str, str | None] = Field(default_factory=dict)
    max_iterations: int = Field(
        default_factory=lambda: int(os.getenv("AI_SCIENTIST_MAX_ITERATIONS", "2")),
        ge=0,
    )
    planning_only: bool = Field(
        default_factory=lambda: os.getenv("AI_SCIENTIST_DEFAULT_PLANNING_ONLY", "true").lower()
        in {"1", "true", "yes", "on"}
    )
    evidence_review_mode: EvidenceReviewMode = "ASSISTED"


class RevisionRequest(StrictModel):
    target: Literal["question", "evidence", "hypothesis", "method", "design", "analysis", "reproducibility"]
    feedback: str


class RevisionReviewSubmitRequest(StrictModel):
    decisions: list[RevisionIssueDecision]


class RevisionReviewDeferRequest(StrictModel):
    reason: str = ""


class ApprovalRequest(StrictModel):
    acknowledged: bool
    expected_versions: dict[str, int | None] = Field(default_factory=dict)


class DeferApprovalRequest(StrictModel):
    reason: str = ""


def _infer_claim_dimension(statement: str) -> ClaimDimension:
    text = statement.lower()
    if any(token in text for token in ["anatom", "structure", "structural", "existence", "解剖", "结构", "实体"]):
        return "structural_existence"
    if any(token in text for token in ["physiolog", "function", "conduction", "pathway", "生理", "传导", "通路"]):
        return "physiological_function"
    if any(token in text for token in ["mechanism", "biophysical", "机制", "生物物理"]):
        return "biophysical_mechanism"
    if any(token in text for token in ["clinical", "efficacy", "therapy", "treatment", "临床", "疗效", "治疗"]):
        return "clinical_efficacy"
    if any(token in text for token in ["safety", "adverse", "风险", "安全"]):
        return "safety"
    if any(token in text for token in ["theory", "concept", "理论", "概念"]):
        return "theoretical_concept"
    return "unspecified"


class ProvideDataRequest(StrictModel):
    artifact_paths: list[str]
    description: str
    data_type: str


class HumanEditRequest(StrictModel):
    patch: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class EvidenceCreateRequest(StrictModel):
    evidence: EvidenceItem
    reason: str = ""


class SearchPlanReviewRequest(StrictModel):
    queries: list[str] | None = None
    auto_approve_future: bool = False


class SourceDecisionInput(StrictModel):
    candidate_id: str
    decision: Literal["keep", "reject", "defer"]
    note: str = ""
    rejection_reason: str = ""


class SourceSelectionRequest(StrictModel):
    decisions: list[SourceDecisionInput]
    selection_note: str = ""


class HumanSourceRequest(StrictModel):
    entries: list[str]


class ResearchAssetUploadRequest(StrictModel):
    filename: str
    content_type: str = "application/octet-stream"
    content_base64: str


class AgentStageResult(StrictModel):
    internal_data: dict[str, Any] = Field(default_factory=dict)
    display_markdown: str = ""
    citations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    requested_model: str = ""
    actual_model: str = ""
    fallback_used: bool = False
    attempted_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    tool_names: list[str] = Field(default_factory=list)
