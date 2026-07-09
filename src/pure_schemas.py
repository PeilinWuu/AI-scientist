"""Schemas for the Pure Qwen Shell API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """One visible chat message. Hidden/system/tool roles are intentionally forbidden."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str


class PureChatRequest(BaseModel):
    """Request accepted by the pure Qwen chat endpoints."""

    model_config = ConfigDict(extra="forbid")

    message: str
    history: list[ChatMessage] = Field(default_factory=list)
    model: str | None = None


class PureChatResponse(BaseModel):
    """Pure Qwen chat response."""

    model_config = ConfigDict(extra="forbid")

    reply: str
    model: str
    mode: str = "pure_qwen"


class DebugPayloadResponse(BaseModel):
    """Payload preview for the exact messages that would be sent to Qwen."""

    model_config = ConfigDict(extra="forbid")

    messages: list[dict[str, str]]
    model: str
    mode: str = "pure_qwen"
