"""Planning-only scientific synthesis role."""

from src.ai_scientist.agents.base_agent import AgentRun, BaseResearchAgent, project_snapshot
from src.ai_scientist.claim_graph import ClaimGraph
from src.ai_scientist.schemas import Conclusion, ResearchProject


class ScientificSynthesizerAgent(BaseResearchAgent[Conclusion]):
    agent_name = "scientific_synthesizer"
    output_model = Conclusion

    def run(self, project: ResearchProject) -> AgentRun[Conclusion]:
        result = super().run(project)
        graph = ClaimGraph(project.evidence, project.claims)
        unsupported_findings = graph.validate_conclusion_traceability(result.output)
        if unsupported_findings:
            result.output.supported_findings = [
                item for item in result.output.supported_findings if item not in unsupported_findings
            ]
            result.output.unsupported_claims.extend(
                item for item in unsupported_findings if item not in result.output.unsupported_claims
            )
        result.output.planning_status_statement = "研究计划已形成，但尚未执行，不能生成实验结论。"
        return result

    def build_payload(self, project: ResearchProject) -> dict:
        payload = project_snapshot(
            project,
            ["question", "evidence", "claims", "hypotheses", "study_design", "analysis_plan", "reviews"],
        )
        payload["execution_status"] = "Planning only; no experiment, simulation, code, or analysis result exists."
        return payload
