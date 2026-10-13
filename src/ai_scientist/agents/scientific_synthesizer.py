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
        executed = project.internal_execution_summary.get("status") == "complete" or any(
            item.get("status") == "success" for item in project.controlled_python_runs
        )
        graph = ClaimGraph(project.evidence, project.claims)
        unsupported_findings = graph.validate_conclusion_traceability(result.output)
        if unsupported_findings:
            unsupported_set = set(unsupported_findings)
            result.output.supported_findings = [
                item for item in result.output.supported_findings if item.statement not in unsupported_set
            ]
            result.output.traceability_pending_findings.extend(
                item for item in unsupported_findings if item not in result.output.traceability_pending_findings
            )
        if determine_output_language(project.objective) == "zh-CN":
            result.output.planning_status_statement = (
                "本报告包含项目内受控执行结果；输入哈希、参数、软件版本和输出校验和均已保存。"
                if executed
                else "本报告是由 AI Scientist 多角色规划与审查形成的研究方案。尚未执行真实实验、仿真或数据分析，因此不能将其视为实验结论。"
            )
        else:
            result.output.planning_status_statement = (
                "This report includes project-internal controlled execution results with saved input hashes, parameters, software versions, and output checksums."
                if executed
                else "This report is a research plan produced through AI Scientist multi-role planning and review. No real experiment, simulation, or data analysis has been executed, so it must not be treated as an experimental conclusion."
            )
        if project.literature_review.get('status') == 'complete':
            result.output.planning_status_statement = '已完成限定范围内的文献证据提取与综合，未开展新实验或数值计算；结论受来源质量与检索覆盖范围限制。'
        return result

    def build_payload(self, project: ResearchProject) -> dict:
        payload = project_snapshot(
            project,
            ["question", "evidence", "claims", "hypotheses", "study_design", "analysis_plan", "reviews", "internal_execution_summary", "controlled_python_runs", "computation", "literature_review"],
        )
        executed = project.internal_execution_summary.get("status") == "complete" or any(
            item.get("status") == "success" for item in project.controlled_python_runs
        )
        payload["execution_status"] = (
            "已完成项目内受控执行；只能依据带执行证据的主张形成结果结论。"
            if executed and determine_output_language(project.objective) == "zh-CN"
            else "Project-internal controlled execution completed; result conclusions must cite execution-backed claims."
            if executed
            else "仅生成研究方案；当前不存在实验、仿真、代码执行或分析结果。"
            if determine_output_language(project.objective) == "zh-CN"
            else "Planning only; no experiment, simulation, code, or analysis result exists."
        )
        if project.literature_review.get('status') == 'complete':
            from src.ai_scientist.literature_execution import synthesis_rows, synthesis_passages
            payload['literature_review'] = {
                **{k: project.literature_review.get(k) for k in (
                    'status', 'report_markdown', 'limitations', 'open_questions', 'final_review_feedback')},
                'rows': synthesis_rows(project),
                'source_read_receipts': synthesis_passages(project),
            }
            payload['execution_status'] = 'Literature synthesis actually completed and independently audited. Use its evidence table and bounded findings. No new experiment or computation executed.'
        return payload
