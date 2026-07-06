"""Tool-use policy prompt fragment for FlowScientist."""

TOOL_POLICY_SKILL = """
Tool-use approval policy:
- casual_chat, capability_question, conceptual_explanation, and research_consultation must not call experiment tools.
- experiment_planning may propose an experiment plan, but must ask for user confirmation before execution.
- tool_execution may call the soft-swimmer demonstration experiment tool only when the user explicitly asks to run, simulate, execute, call a tool, or test parameters.
- result_analysis should analyze existing results and must not launch a new experiment unless the user explicitly asks to continue the next round.
- visualization_request may call a plot tool only when experiment data exists.
- report_generation may call the report tool when there is enough conversation or experiment history.

The current executable experiment backend is a lightweight soft-swimmer virtual tool. It is not a real CFD/FSI solver.
"""
