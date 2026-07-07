"""Prompt skill modules for the FlowScientist dialogue agent."""

from src.skills.base_dialogue_skill import BASE_DIALOGUE_SKILL, TOOL_USE_POLICY
from src.skills.capability_skill import CAPABILITY_SKILL
from src.skills.conceptual_explanation_skill import CONCEPTUAL_EXPLANATION_SKILL
from src.skills.experiment_planning_skill import EXPERIMENT_PLANNING_SKILL
from src.skills.intent_router_skill import INTENT_ROUTER_SKILL, INTENTS
from src.skills.report_skill import REPORT_SKILL
from src.skills.research_consultation_skill import RESEARCH_CONSULTATION_SKILL
from src.skills.result_analysis_skill import RESULT_ANALYSIS_SKILL
from src.skills.readable_response_skill import READABLE_RESPONSE_SKILL
from src.skills.tool_execution_skill import TOOL_EXECUTION_SKILL
from src.skills.tool_policy_skill import TOOL_POLICY_SKILL
from src.skills.visualization_skill import VISUALIZATION_SKILL


SKILL_BY_INTENT = {
    "casual_chat": BASE_DIALOGUE_SKILL,
    "capability_question": CAPABILITY_SKILL,
    "conceptual_explanation": CONCEPTUAL_EXPLANATION_SKILL,
    "research_consultation": RESEARCH_CONSULTATION_SKILL,
    "experiment_planning": EXPERIMENT_PLANNING_SKILL,
    "tool_execution": TOOL_EXECUTION_SKILL,
    "result_analysis": RESULT_ANALYSIS_SKILL,
    "visualization_request": VISUALIZATION_SKILL,
    "report_generation": REPORT_SKILL,
    "web_research": RESEARCH_CONSULTATION_SKILL,
    "literature_search": RESEARCH_CONSULTATION_SKILL,
    "documentation_lookup": RESEARCH_CONSULTATION_SKILL,
    "current_info_lookup": RESEARCH_CONSULTATION_SKILL,
}


def get_skill_prompt(intent: str) -> str:
    """Return the prompt skill best suited for the classified intent."""

    return SKILL_BY_INTENT.get(intent, BASE_DIALOGUE_SKILL)


__all__ = [
    "BASE_DIALOGUE_SKILL",
    "CAPABILITY_SKILL",
    "CONCEPTUAL_EXPLANATION_SKILL",
    "EXPERIMENT_PLANNING_SKILL",
    "INTENT_ROUTER_SKILL",
    "INTENTS",
    "REPORT_SKILL",
    "RESEARCH_CONSULTATION_SKILL",
    "READABLE_RESPONSE_SKILL",
    "RESULT_ANALYSIS_SKILL",
    "SKILL_BY_INTENT",
    "TOOL_USE_POLICY",
    "TOOL_POLICY_SKILL",
    "TOOL_EXECUTION_SKILL",
    "VISUALIZATION_SKILL",
    "get_skill_prompt",
]
