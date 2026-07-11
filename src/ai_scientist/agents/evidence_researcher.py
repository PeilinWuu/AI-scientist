"""Evidence discovery and claim extraction role."""

from __future__ import annotations

from src.ai_scientist.agents.base_agent import AgentRun, BaseResearchAgent, project_snapshot
from src.ai_scientist.exceptions import StructuredOutputError
from src.ai_scientist.schemas import EvidenceResearchOutput, ResearchProject
from src.ai_scientist.tools.search_tools import QwenEvidenceSearchTool


class EvidenceResearcherAgent(BaseResearchAgent[EvidenceResearchOutput]):
    agent_name = "evidence_researcher"
    output_model = EvidenceResearchOutput

    def __init__(self, *args: object, search_tool: QwenEvidenceSearchTool | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.search_tool = search_tool or QwenEvidenceSearchTool()

    def run(self, project: ResearchProject) -> AgentRun[EvidenceResearchOutput]:
        if project.question is None:
            raise StructuredOutputError("Evidence research requires a formulated research question.")
        search_result = self.search_tool.run(project.question.normalized_question)
        explicit_sources = search_result.get("sources") or []
        payload = self.build_payload(project)
        payload["search_result"] = {
            "answer": search_result.get("reply", ""),
            "sources": explicit_sources,
            "search_used": search_result.get("search_used", False),
            "source_rule": (
                "Only create sourced evidence from the explicit sources array. "
                "If it is empty, keep claims unsupported and do not invent URLs or citations."
            ),
        }
        domain_skill = "general" if project.domain == "mathematics" else project.domain
        skills = self.skill_loader.load_for_agent(self.agent_name, project.research_mode, domain_skill)
        result = self.client.call(
            self.agent_name,
            self.skill_loader.compose_instructions(skills),
            payload,
            self.output_model,
        )
        allowed_urls = {str(source.get("url")) for source in explicit_sources if source.get("url")}
        for evidence in result.value.evidence:
            if evidence.source_url and evidence.source_url not in allowed_urls:
                raise StructuredOutputError("Evidence researcher produced a URL not returned by the search tool.")
        evidence_ids = {item.evidence_id for item in result.value.evidence}
        for claim in result.value.claims:
            if claim.status == "supported" and not set(claim.supporting_evidence_ids) & evidence_ids:
                raise StructuredOutputError("Evidence researcher marked a claim supported without linked evidence.")
        return AgentRun(
            output=result.value,
            metadata=result.metadata,
            tool_names=["web_search", "web_extractor"],
            auxiliary={
                "response_id": search_result.get("response_id"),
                "search_model_calls": 1,
                "query_count": 1,
                "search_result_count": len(explicit_sources),
                "extracted_page_count": int((search_result.get("tool_usage") or {}).get("web_extractor", 0)),
            },
        )

    def build_payload(self, project: ResearchProject) -> dict:
        return project_snapshot(project, ["question", "domain", "research_mode", "constraints"])
