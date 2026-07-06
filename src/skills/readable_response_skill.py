"""Readable-response prompt fragment for FlowScientist."""

READABLE_RESPONSE_SKILL = """
Readable response rules:
- assistant_message must be natural language, not raw JSON.
- Do not paste complete tool results into chat.
- Summarize best candidates, constraint status, trends, and next steps.
- Put raw payloads into audit logs or collapsible raw_data fields.
- If the user asks a capability or conceptual question, answer directly and do not mention internal JSON.
"""
