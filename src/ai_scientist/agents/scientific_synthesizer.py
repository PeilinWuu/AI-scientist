"""Planning-only scientific synthesis role."""

from src.ai_scientist.agents.base_agent import AgentRun, BaseResearchAgent, project_snapshot
from src.ai_scientist.claim_graph import ClaimGraph
from src.ai_scientist.presentation import determine_output_language
from src.ai_scientist.schemas import Conclusion, ResearchProject


class ScientificSynthesizerAgent(BaseResearchAgent[Conclusion]):
    agent_name = "scientific_synthesizer"
    output_model = Conclusion

    def run(self, project: ResearchProject) -> AgentRun[Conclusion]:
        result = super().run(project)
        graph = ClaimGraph(project.evidence, project.claims)
        unsupported_findings = graph.validate_conclusion_traceability(result.output)
        if unsupported_findings:
            unsupported_set = set(unsupported_findings)
            result.output.supported_findings = [
                item for item in result.output.supported_findings if item.statement not in unsupported_set
            ]
            result.output.unsupported_claims.extend(
                item for item in unsupported_findings if item not in result.output.unsupported_claims
            )
        if determine_output_language(project.objective) == "zh-CN":
            result.output.planning_status_statement = (
                "本报告是由 AI Scientist 多角色规划与审查形成的研究方案。"
                "尚未执行真实实验、仿真或数据分析，因此不能将其视为实验结论。"
            )
        else:
            result.output.planning_status_statement = (
                "This report is a research plan produced through AI Scientist multi-role planning and review. "
                "No real experiment, simulation, or data analysis has been executed, so it must not be treated "
                "as an experimental conclusion."
            )
        return result

    def build_payload(self, project: ResearchProject) -> dict:
        payload = project_snapshot(
            project,
            ["question", "evidence", "claims", "hypotheses", "study_design", "analysis_plan", "reviews"],
        )
        payload["execution_status"] = (
            "仅生成研究方案；当前不存在实验、仿真、代码执行或分析结果。"
            if determine_output_language(project.objective) == "zh-CN"
            else "Planning only; no experiment, simulation, code, or analysis result exists."
        )
        return payload
