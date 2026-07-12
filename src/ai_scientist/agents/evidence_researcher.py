"""Evidence discovery and claim extraction role."""

from __future__ import annotations

from src.ai_scientist.agents.base_agent import AgentRun, BaseResearchAgent, project_snapshot
from src.ai_scientist.domain_resolution import canonicalize_domain
from src.ai_scientist.exceptions import StructuredOutputError
from src.ai_scientist.schemas import EvidenceResearchOutput, ResearchProject, SearchAcquisitionResult, SearchSource
from src.ai_scientist.tools.search_tools import QwenEvidenceSearchTool


class EvidenceResearcherAgent(BaseResearchAgent[EvidenceResearchOutput]):
    agent_name = "evidence_researcher"
    output_model = EvidenceResearchOutput

    def __init__(self, *args: object, search_tool: QwenEvidenceSearchTool | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.search_tool = search_tool or QwenEvidenceSearchTool()

    def run(self, project: ResearchProject) -> AgentRun[EvidenceResearchOutput]:
        acquisition = self.acquire_search(project)
        return self.normalize_search_result(project, acquisition)

    def acquire_search(self, project: ResearchProject) -> SearchAcquisitionResult:
        """Run web search and store only final answer text plus explicit sources."""

        if project.question is None:
            raise StructuredOutputError("Evidence research requires a formulated research question.")
        query = self._search_query(project)
        search_result = self.search_tool.run(
            query,
            previous_response_id=project.previous_response_ids.get("evidence_search"),
        )
        sources = [SearchSource.model_validate(item) for item in (search_result.get("sources") or [])]
        warnings: list[str] = []
        if not sources:
            warnings.append("No explicit source metadata was returned by the search provider.")
        return SearchAcquisitionResult(
            final_text=str(search_result.get("reply") or ""),
            sources=sources,
            response_id=search_result.get("response_id") if isinstance(search_result.get("response_id"), str) else None,
            request_id=search_result.get("request_id") if isinstance(search_result.get("request_id"), str) else None,
            search_used=bool(search_result.get("search_used")),
            warnings=warnings,
        )

    def normalize_search_result(
        self,
        project: ResearchProject,
        acquisition: SearchAcquisitionResult,
    ) -> AgentRun[EvidenceResearchOutput]:
        """Ask the research model to turn search text into safe evidence records."""

        if project.question is None:
            raise StructuredOutputError("Evidence normalization requires a formulated research question.")
        payload = self.build_payload(project)
        payload["search_acquisition"] = {
            "final_text": acquisition.final_text,
            "sources": [item.model_dump(mode="json") for item in acquisition.sources],
            "search_used": acquisition.search_used,
            "warnings": acquisition.warnings,
            "source_rule": (
                "Only create sourced evidence from the explicit sources array. "
                "If it is empty, evidence may be summarized as unverified, but do not invent URLs, DOI, authors, or citations."
            ),
        }
        domain_skill = (
            project.domain_resolution.loaded_domain_skill
            if project.domain_resolution is not None
            else canonicalize_domain(project.domain)
        )
        skills = self.skill_loader.load_for_agent(self.agent_name, project.research_mode, domain_skill)
        result = self.client.call(
            self.agent_name,
            self.skill_loader.compose_instructions(skills),
            payload,
            self.output_model,
        )
        allowed_urls = {source.url for source in acquisition.sources if source.url}
        for evidence in result.value.evidence:
            if evidence.source_url and evidence.source_url not in allowed_urls:
                evidence.source_url = None
                evidence.verified = False
                evidence.verification_note = "The model produced a URL that was not returned by search acquisition."
            if not evidence.source_url:
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
            tool_names=["web_search", "web_extractor"],
            auxiliary={
                "response_id": acquisition.response_id,
                "query_count": 1,
                "search_result_count": len(acquisition.sources),
                "search_used": acquisition.search_used,
                "search_warnings": acquisition.warnings,
            },
        )

    def build_payload(self, project: ResearchProject) -> dict:
        return project_snapshot(
            project,
            ["question", "domain", "secondary_domains", "domain_resolution", "research_mode", "constraints"],
        )

    def _search_query(self, project: ResearchProject) -> str:
        question = project.question.normalized_question if project.question else project.objective
        secondary = ", ".join(project.secondary_domains[:3])
        scope = project.question.scope if project.question else ""
        return "\n".join(
            line
            for line in [
                f"Research question: {question}",
                f"Scope: {scope}" if scope else "",
                f"Primary domain: {project.domain}",
                f"Secondary domains: {secondary}" if secondary else "",
                "Find credible background evidence, competing claims, limitations, and source metadata.",
            ]
            if line
        )
