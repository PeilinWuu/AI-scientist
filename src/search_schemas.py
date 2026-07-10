"""Schemas for optional Qwen search mode."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """One visible chat message. Hidden/system/tool roles are intentionally forbidden."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str


class SearchChatRequest(BaseModel):
    """Request accepted by optional Qwen search endpoints."""

    model_config = ConfigDict(extra="forbid")

    message: str
    history: list[ChatMessage] = Field(default_factory=list)
    model: str | None = None


class SearchSource(BaseModel):
    """One search source returned by the provider, if available."""

    model_config = ConfigDict(extra="forbid")

    site_name: str = ""
    title: str = ""
    url: str = ""
    snippet: str = ""
    index: int


class SearchChatResponse(BaseModel):
    """Qwen search response."""

    model_config = ConfigDict(extra="forbid")

    reply: str
    model: str
    mode: str = "qwen_search"
    search_enabled: bool = True
    search_forced: bool = True
    search_strategy: str = "turbo"
    search_method: str = "chat_completions_enable_search_forced"
    search_effective: bool | None = None
    source_metadata_available: bool = False
    sources: list[SearchSource] = Field(default_factory=list)
    warning: str | None = None
    request_id: str | None = None


class SearchDebugPayloadResponse(BaseModel):
    """Payload preview for the exact search-mode request body additions."""

    model_config = ConfigDict(extra="forbid")

    messages: list[dict[str, str]]
    model: str
    mode: str = "qwen_search"
    extra_body: dict
