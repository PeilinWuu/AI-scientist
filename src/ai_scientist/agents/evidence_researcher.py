"""Bounded evidence discovery, extraction, and offline normalization role."""

from __future__ import annotations

import os

from src.ai_scientist.agents.base_agent import (
    AgentRun,
    BaseResearchAgent,
    parsed_asset_context,
    project_snapshot,
)
from src.ai_scientist.domain_resolution import canonicalize_domain
from src.ai_scientist.exceptions import StructuredOutputError
from src.ai_scientist.model_registry import ModelRegistry
from src.ai_scientist.schemas import (
    EvidenceResearchOutput,
    ResearchProject,
    SearchAcquisitionResult,
    SearchCandidate,
    SearchPlan,
    SearchPlanRelevanceValidation,
    SearchSource,
)
from src.ai_scientist.tools.search_tools import QwenEvidenceSearchTool


class EvidenceResearcherAgent(BaseResearchAgent[EvidenceResearchOutput]):
    agent_name = "evidence_researcher"
    output_model = EvidenceResearchOutput

    def __init__(self, *args: object, search_tool: QwenEvidenceSearchTool | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.search_tool = search_tool or QwenEvidenceSearchTool()

    def run(self, project: ResearchProject) -> AgentRun[EvidenceResearchOutput]:
        raise StructuredOutputError(
            "Evidence Researcher must run through the orchestrator's search-plan and source-review gates."
        )

    def plan_search(self, project: ResearchProject) -> AgentRun[SearchPlan]:
        """Create a compact search plan without enabling network tools."""

        if project.question is None:
            raise StructuredOutputError("Evidence research requires a formulated research question.")
        maximum = max(1, int(os.getenv("AI_SCIENTIST_MAX_SEARCH_QUERIES", "4")))
        research_mode = getattr(project, "research_mode", None)
        payload = {
            "project_id": project.project_id,
            "research_question": project.question.model_dump(mode="json"),
            "normalized_question": project.question.normalized_question,
            "research_mode": research_mode.value if research_mode else None,
            "domain": project.domain,
            "evidence_gaps": getattr(project, "evidence_gaps", []),
            "constraints": getattr(project, "constraints", {}),
            "source_review_feedback_summary": project.source_review_feedback.model_dump(mode="json"),
            "maximum_queries": maximum,
            "task": (
                "Produce compact bibliographic search queries only. Prefer queries that discriminate among claims. "
                "Do not summarize evidence, map claims, grade sources, or write conclusions."
            ),
        }
        asset_context = parsed_asset_context(project)
        if asset_context["assets"]:
            payload["uploaded_asset_context"] = asset_context
        skills = self._skills(project)
        result = self.client.call(
            self.agent_name,
            self.skill_loader.compose_instructions(skills),
            payload,
            SearchPlan,
        )
        return AgentRun(output=result.value, metadata=result.metadata)

    def validate_search_plan_semantics(
        self, project: ResearchProject, plan: SearchPlan
    ) -> AgentRun[SearchPlanRelevanceValidation]:
        """Use one small structured call only to classify plan relevance."""

        result = self.client.call(
            self.agent_name,
            "Judge whether bibliographic queries target the supplied research question. Return JSON only.",
            {
                "research_question": project.question.model_dump(mode="json") if project.question else None,
                "domain": project.domain,
                "queries": plan.queries,
                "allowed_status": ["relevant", "partially_relevant", "irrelevant"],
            },
            SearchPlanRelevanceValidation,
        )
        return AgentRun(output=result.value, metadata=result.metadata)

    def search_one_query(self, query: str, search_model: str) -> dict[str, object]:
        return self.search_tool.search_query(query, search_model)

    def extract_candidate_batch(
        self,
        candidates: list[SearchCandidate],
        search_model: str,
        progress_callback=None,
    ) -> dict[str, object]:
        return self.search_tool.extract_sources(candidates, search_model, progress_callback)

    def acquire_search(self, project: ResearchProject, search_model: str) -> SearchAcquisitionResult:
        """Compatibility path: bounded plan and independent web-search calls, without extraction."""

        if not hasattr(self.search_tool, "search_query"):
            result = self.search_tool.run(
                self._search_query(project), model=search_model, previous_response_id=None
            )
            return SearchAcquisitionResult(
                final_text=str(result.get("reply") or ""),
                sources=result.get("sources") or [],
                response_id=result.get("response_id"),
                request_id=result.get("request_id"),
                search_used=bool(result.get("search_used")),
            )
        plan_run = self.plan_search(project)
        candidates: list[SearchCandidate] = []
        text_parts: list[str] = []
        for query in plan_run.output.queries:
            result = self.search_one_query(query, search_model)
            candidates.extend(SearchCandidate.model_validate(item) for item in result.get("candidates") or [])
            if result.get("final_text"):
                text_parts.append(str(result["final_text"]))
        sources = [
            SearchSource(title=item.title, url=item.url, site_name=item.source_domain, snippet=item.snippet)
            for item in candidates
        ]
        return SearchAcquisitionResult(
            final_text="\n\n".join(text_parts),
            sources=sources,
            search_used=bool(candidates),
            search_plan=plan_run.output,
            candidates=candidates,
            selected_candidates=candidates,
            usable_source_count=len(candidates),
            requested_model=search_model,
            actual_model=search_model,
            warnings=[] if candidates else ["No explicit source metadata was returned by the search provider."],
        )

    def normalize_search_result(
        self,
        project: ResearchProject,
        acquisition: SearchAcquisitionResult,
    ) -> AgentRun[EvidenceResearchOutput]:
        """Normalize selected metadata and extracted text with no network tools."""

        if project.question is None:
            raise StructuredOutputError("Evidence normalization requires a formulated research question.")
        payload = {
            "research_question": project.question.model_dump(mode="json"),
            "selected_sources": [item.model_dump(mode="json") for item in acquisition.selected_candidates],
            "extracted_source_content": acquisition.final_text,
            "warnings": acquisition.warnings,
            "source_rule": (
                "Only create sourced evidence from selected_sources. Never invent URLs, DOI, PMID, authors, "
                "publication details, or citations. Parsed uploaded references may be represented with their "
                "exact source_asset_id, but must remain independently unverified. Uploaded datasets are observed "
                "data inputs, not literature evidence. Do not perform or request web search."
            ),
        }
        asset_context = parsed_asset_context(project)
        if asset_context["assets"]:
            payload["uploaded_asset_context"] = asset_context
        result = self.client.call(
            self.agent_name,
            self.skill_loader.compose_instructions(self._skills(project)),
            payload,
            self.output_model,
        )
        allowed_urls = {source.url for source in acquisition.selected_candidates if source.url}
        allowed_assets = {
            asset.asset_id: asset
            for asset in getattr(project, "research_assets", [])
            if asset.parsing_status == "parsed" and asset.parsed_content is not None
        }
        for evidence in result.value.evidence:
            if evidence.source_asset_id and evidence.source_asset_id not in allowed_assets:
                evidence.source_asset_id = None
                evidence.verified = False
                evidence.verification_note = "The model referenced an unknown uploaded asset identifier."
            if evidence.source_asset_id in allowed_assets:
                source_asset = allowed_assets[evidence.source_asset_id]
                evidence.source_type = (
                    "uploaded_dataset" if source_asset.purpose == "data" else "uploaded_reference"
                )
                evidence.verified = False
                evidence.verification_note = (
                    "User-provided parsed asset; parsing preserves provenance but is not independent source verification."
                )
            if evidence.source_url and evidence.source_url not in allowed_urls:
                evidence.source_url = None
                evidence.verified = False
                evidence.verification_note = "The model produced a URL that was not returned by selected search acquisition sources."
            if not evidence.source_url and not evidence.source_asset_id:
                evidence.verified = False
                evidence.verification_note = evidence.verification_note or "No verifiable URL was returned."
        evidence_ids = {item.evidence_id for item in result.value.evidence}
        for claim in result.value.claims:
            if claim.status == "supported" and not set(claim.supporting_evidence_ids) & evidence_ids:
                claim.status = "unknown"
                claim.limitations.append("The model did not link this claim to a validated evidence record.")
        return AgentRun(
            output=result.value,
            metadata=result.metadata,
            tool_names=[],
            auxiliary={
                "query_count": len(acquisition.query_records or (acquisition.search_plan.queries if acquisition.search_plan else [])),
                "search_result_count": len(acquisition.candidates),
                "extracted_page_count": acquisition.usable_source_count,
                "search_used": acquisition.search_used,
                "search_warnings": acquisition.warnings,
                "parsed_asset_ids": [item["asset_id"] for item in asset_context["assets"]],
                "parsed_artifact_ids": [
                    item["parsed_artifact_id"]
                    for item in asset_context["assets"]
                    if item.get("parsed_artifact_id")
                ],
            },
        )

    def build_payload(self, project: ResearchProject) -> dict:
        return project_snapshot(
            project,
            ["question", "domain", "secondary_domains", "domain_resolution", "research_mode", "constraints"],
        )

    def _skills(self, project: ResearchProject) -> list[dict]:
        domain_skill = (
            project.domain_resolution.loaded_domain_skill
            if project.domain_resolution is not None
            else canonicalize_domain(project.domain)
        )
        return self.skill_loader.load_for_agent(self.agent_name, project.research_mode, domain_skill)

    @staticmethod
    def _search_query(project: ResearchProject) -> str:
        question = project.question.normalized_question if project.question else project.objective
        return f"{question} credible primary studies systematic reviews source metadata"
