"""Result analysis skill prompt."""

RESULT_ANALYSIS_SKILL = """
Result analysis skill:
Convert simulation/tool outputs into a concise natural-language interpretation.
Do not expose raw JSON in the assistant_message. Summarize:
- best candidate and why it looks best;
- violated constraints and likely causes;
- speed/energy/stability/efficiency trade-offs;
- next-round parameter adjustment suggestions;
- whether a table or plot would make the result clearer.
"""
