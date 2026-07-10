"""Domain-neutral structured objects used by the AI Scientist workflow."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import os
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    CLAIM_EVIDENCE_MAPPING = "CLAIM_EVIDENCE_MAPPING"
    HYPOTHESIS_GENERATION = "HYPOTHESIS_GENERATION"
    METHOD_SELECTION = "METHOD_SELECTION"
    STUDY_DESIGN = "STUDY_DESIGN"
    ANALYSIS_PLANNING = "ANALYSIS_PLANNING"
    FEASIBILITY_REVIEW = "FEASIBILITY_REVIEW"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
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


class EvidenceItem(StrictModel):
    evidence_id: str = Field(default_factory=lambda: new_id("evidence"))
    title: str
    source_type: str
    source_url: str | None = None
    citation: str | None = None
    summary: str
    extracted_claims: list[str] = Field(default_factory=list)
    reliability: str = "unknown"
    relevance: str = "unknown"
    limitations: list[str] = Field(default_factory=list)
    publication_date: str | None = None
    status: str = "unverified"


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
        return self


class Conclusion(StrictModel):
    planning_status_statement: str = "研究计划已形成，但尚未执行，不能生成实验结论。"
    supported_findings: list[str] = Field(default_factory=list)
    tentative_inferences: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    negative_results: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    scope_of_validity: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    next_questions: list[str] = Field(default_factory=list)
    human_verification_required: list[str] = Field(default_factory=list)


class BudgetState(StrictModel):
    max_model_calls: int = Field(default=50, ge=1)
    used_model_calls: int = Field(default=0, ge=0)
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
    schema_valid: bool = True
    tool_names: list[str] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None


class ResearchProject(StrictModel):
    project_id: str = Field(default_factory=lambda: new_id("project"))
    title: str
    objective: str
    domain: str = "general"
    secondary_domains: list[str] = Field(default_factory=list)
    research_mode: ResearchMode | None = None
    secondary_modes: list[ResearchMode] = Field(default_factory=list)
    phase: ResearchPhase = ResearchPhase.INTAKE
    constraints: dict[str, Any] = Field(default_factory=dict)
    question: ResearchQuestion | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    study_design: StudyDesign | None = None
    analysis_plan: AnalysisPlan | None = None
    reproducibility_plan: dict[str, Any] = Field(default_factory=dict)
    reviews: list[ReviewResult] = Field(default_factory=list)
    conclusion: Conclusion | None = None
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    iteration: int = 0
    max_iterations: int = 2
    budget: BudgetState = Field(default_factory=BudgetState)
    available_tools: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    human_actions_required: list[str] = Field(default_factory=list)
    previous_response_ids: dict[str, str] = Field(default_factory=dict)
    planning_only: bool = True
    domain_hint: str | None = None
    method_rationale: str = ""
    validity_threats: list[str] = Field(default_factory=list)
    required_controls: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    conflicting_evidence: list[str] = Field(default_factory=list)
    revision_feedback: list[str] = Field(default_factory=list)
    pending_revision_target: str | None = None
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
    constraints: dict[str, Any] = Field(default_factory=dict)
    max_iterations: int = Field(
        default_factory=lambda: int(os.getenv("AI_SCIENTIST_MAX_ITERATIONS", "2")),
        ge=0,
    )
    planning_only: bool = Field(
        default_factory=lambda: os.getenv("AI_SCIENTIST_DEFAULT_PLANNING_ONLY", "true").lower()
        in {"1", "true", "yes", "on"}
    )


class RevisionRequest(StrictModel):
    target: Literal["question", "evidence", "hypothesis", "method", "design"]
    feedback: str


class ProvideDataRequest(StrictModel):
    artifact_paths: list[str]
    description: str
    data_type: str
