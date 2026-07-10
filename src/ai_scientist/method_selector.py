"""Multi-signal research-mode selection independent of any one domain."""

from __future__ import annotations

from src.ai_scientist.schemas import MethodSelectionOutput, ResearchMode, ResearchQuestion


class MethodSelector:
    """Rank research modes from objectives, data, capabilities, and constraints."""

    def select(
        self,
        research_question: ResearchQuestion,
        objective: str,
        available_data: list[str],
        available_tools: list[str],
        constraints: dict[str, object],
    ) -> MethodSelectionOutput:
        text = " ".join(
            [
                research_question.original_question,
                research_question.normalized_question,
                research_question.question_type,
                objective,
                " ".join(research_question.operational_definitions),
                " ".join(available_data),
                " ".join(str(item) for item in constraints.values()),
            ]
        ).lower()
        scores = {mode: 0.0 for mode in ResearchMode}
        signal_groups = {
            ResearchMode.THEORETICAL: ["prove", "proof", "theorem", "uniqueness", "existence", "唯一性", "证明", "定理", "解析"],
            ResearchMode.OBSERVATIONAL: ["association", "population", "survey", "observational", "是否提高", "影响", "生产率", "队列"],
            ResearchMode.COMPUTATIONAL_EXPERIMENT: ["algorithm", "classifier", "benchmark", "dataset", "算法", "分类", "模型评估", "数据集"],
            ResearchMode.CONTROLLED_EXPERIMENT: [
                "intervention", "treatment", "catalyst", "controlled", "control structure",
                "催化", "反应", "实验条件", "被动控制", "主动控制",
            ],
            ResearchMode.SYSTEMATIC_REVIEW: [
                "systematic review", "literature evidence", "meta-analysis",
                "系统整理", "系统综述", "证据整理", "检索式", "纳入排除", "证据分级",
                "系统", "整理", "证据",
            ],
            ResearchMode.SIMULATION: ["simulation", "numerical model", "仿真", "数值模拟", "模型预测"],
            ResearchMode.ENGINEERING_DESIGN: ["prototype", "optimize design", "engineering design", "设计方案", "优化结构", "原型"],
            ResearchMode.DATA_ANALYSIS: ["analyze dataset", "existing data", "统计分析", "已有数据", "数据分析"],
            ResearchMode.MIXED_METHODS: ["mixed methods", "qualitative and quantitative", "混合方法", "定性和定量"],
        }
        for mode, signals in signal_groups.items():
            scores[mode] += sum(1.0 for signal in signals if signal in text)
        if available_data:
            scores[ResearchMode.DATA_ANALYSIS] += 1.5
            scores[ResearchMode.OBSERVATIONAL] += 0.5
        if "python_executor" in available_tools or "code_runner" in available_tools:
            scores[ResearchMode.COMPUTATIONAL_EXPERIMENT] += 0.5
        if constraints.get("no_intervention"):
            scores[ResearchMode.OBSERVATIONAL] += 2
            scores[ResearchMode.CONTROLLED_EXPERIMENT] -= 2
        ranked = sorted(scores, key=scores.get, reverse=True)
        primary = ranked[0]
        if scores[primary] <= 0:
            primary = ResearchMode.MIXED_METHODS
        secondary = [mode for mode in ranked if mode != primary and scores[mode] >= max(1, scores[primary] - 1)][:2]
        required_tools = _required_tools(primary)
        unavailable = [tool for tool in required_tools if tool not in available_tools]
        return MethodSelectionOutput(
            primary_research_mode=primary,
            secondary_modes=secondary,
            rationale=(
                f"Selected {primary.value} from multiple signals in the objective, question form, "
                f"available data, tools, and constraints; mode score={scores[primary]:.1f}."
            ),
            required_methods=_required_methods(primary),
            required_tools=required_tools,
            unavailable_capabilities=unavailable,
            human_actions_required=[f"Provide or connect capability: {item}" for item in unavailable],
            confidence=min(0.95, 0.45 + max(0, scores[primary]) * 0.1),
        )


def _required_tools(mode: ResearchMode) -> list[str]:
    return {
        ResearchMode.THEORETICAL: ["file_search"],
        ResearchMode.CONTROLLED_EXPERIMENT: ["artifact_store"],
        ResearchMode.OBSERVATIONAL: ["dataset_inspector", "statistical_analyzer"],
        ResearchMode.COMPUTATIONAL_EXPERIMENT: ["dataset_inspector", "code_runner"],
        ResearchMode.SIMULATION: ["code_runner"],
        ResearchMode.DATA_ANALYSIS: ["dataset_inspector", "statistical_analyzer"],
        ResearchMode.SYSTEMATIC_REVIEW: ["web_search", "citation_manager"],
        ResearchMode.ENGINEERING_DESIGN: ["artifact_store"],
        ResearchMode.MIXED_METHODS: ["dataset_inspector", "artifact_store"],
    }[mode]


def _required_methods(mode: ResearchMode) -> list[str]:
    return {
        ResearchMode.THEORETICAL: ["definitions", "proof obligations", "counterexample search"],
        ResearchMode.CONTROLLED_EXPERIMENT: ["controls", "randomization", "measurement error assessment"],
        ResearchMode.OBSERVATIONAL: ["confounder analysis", "identification strategy", "sensitivity checks"],
        ResearchMode.COMPUTATIONAL_EXPERIMENT: ["data split", "baselines", "ablation", "repeated runs"],
        ResearchMode.SIMULATION: ["model verification", "convergence", "sensitivity", "validation"],
        ResearchMode.DATA_ANALYSIS: ["assumption checks", "robustness", "uncertainty quantification"],
        ResearchMode.SYSTEMATIC_REVIEW: ["search protocol", "eligibility criteria", "bias assessment"],
        ResearchMode.ENGINEERING_DESIGN: ["requirements", "trade-off analysis", "verification plan"],
        ResearchMode.MIXED_METHODS: ["method integration", "triangulation", "discordance analysis"],
    }[mode]
