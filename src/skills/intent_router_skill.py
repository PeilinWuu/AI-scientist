"""Intent definitions for FlowScientist."""

INTENTS = [
    "casual_chat",
    "capability_question",
    "conceptual_explanation",
    "research_consultation",
    "experiment_planning",
    "tool_execution",
    "result_analysis",
    "visualization_request",
    "report_generation",
    "web_research",
    "literature_search",
    "documentation_lookup",
    "current_info_lookup",
]

INTENT_ROUTER_SKILL = """
Classify the user's latest message into exactly one intent:
- casual_chat: greeting, thanks, small talk.
- capability_question: asks what the system can do, why it is useful, project meaning, boundaries, or advantages.
- conceptual_explanation: asks for concepts, principles, why/how explanations.
- research_consultation: describes a research direction without asking to run or plan a concrete experiment.
- experiment_planning: asks to design an experiment plan, parameter space, or next-step plan, but does not authorize execution.
- tool_execution: explicitly asks to run an experiment/simulation/tool or test parameters.
- result_analysis: provides results or asks about existing results.
- visualization_request: asks for plot/chart/curve/scatter/visualization/comparison figure.
- report_generation: asks for a report, summary, PPT material, or research plan document.
- web_research: explicitly asks to search online, browse the web, verify external public information, or check online sources.
- literature_search: explicitly asks to find papers, recent papers, literature, citations, or scholarly references.
- documentation_lookup: explicitly asks to check official documentation, API docs, model docs, or package docs.
- current_info_lookup: explicitly asks for latest/current/recent information, current versions, 2025/2026 updates, or verification.

Only tool_execution may run the experiment tool. Visualization_request may run a
plot tool only when usable data exists. Experiment_planning should propose a plan
and ask for confirmation.
"""
