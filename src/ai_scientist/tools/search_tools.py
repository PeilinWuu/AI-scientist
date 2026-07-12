"""Safe reuse of the existing Qwen Search client for evidence discovery."""

from __future__ import annotations

from src.search_qwen_client import SearchQwenClient


class QwenEvidenceSearchTool:
    name = "web_search"

    def run(
        self,
        query: str,
        model: str,
        previous_response_id: str | None = None,
    ) -> dict[str, object]:
        client = SearchQwenClient(timeout_env="AI_SCIENTIST_SEARCH_TIMEOUT")
        return client.search(message=query, model=model, previous_response_id=previous_response_id)
