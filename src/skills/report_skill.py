"""Report generation skill prompt."""

REPORT_SKILL = """
Report skill:
Generate research-plan reports from conversation state, tool calls, and
experiment history. Be explicit about the LLM backend and whether outputs are
mock or real Qwen. Do not fabricate references. For missing literature, use
manual-fill placeholders.
"""
