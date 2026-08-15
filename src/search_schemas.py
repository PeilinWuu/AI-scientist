"""Schemas for optional Qwen Responses API search mode."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SearchChatRequest(BaseModel):
    """One search turn, optionally linked to the preceding Responses result."""

    model_config = ConfigDict(extra="forbid")

    message: str
    model: str | None = None
    previous_response_id: str | None = None


class SearchSource(BaseModel):
    """One explicit source citation returned by the provider."""

    model_config = ConfigDict(extra="forbid")

    site_name: str = ""
    title: str = ""
    url: str = ""
    snippet: str = ""
    index: int


class SearchToolUsage(BaseModel):
    """Counts of built-in tools observed in the provider response."""

    model_config = ConfigDict(extra="forbid")

    web_search: int = 0
    web_extractor: int = 0


class SearchChatResponse(BaseModel):
    """Public search response with final text and separate audit metadata."""

    model_config = ConfigDict(extra="forbid")

    reply: str
    model: str
    mode: str = "qwen_search"
    response_id: str | None = None
    request_id: str | None = None
    search_used: bool
    sources: list[SearchSource] = Field(default_factory=list)
    tool_usage: SearchToolUsage = Field(default_factory=SearchToolUsage)


class SearchDebugPayloadResponse(BaseModel):
    """Exact safe preview of a Responses API search request."""

    model_config = ConfigDict(extra="forbid")

    model: str
    mode: str = "qwen_search"
    input: str
    previous_response_id: str | None = None
    tools: list[dict[str, str]]
