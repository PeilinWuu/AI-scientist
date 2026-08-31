"""Minimal OpenAI-compatible Qwen client for Pure Qwen Shell mode."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import OpenAI

from src.pure_schemas import ChatMessage


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class PureQwenClient:
    """Call Qwen without hidden system prompts, search, or response shaping."""

    def __init__(self) -> None:
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY is missing. Please set it in .env.")

        self.api_key = api_key
        self.model = os.getenv("LLM_MODEL", "qwen3.8-max")
        self.base_url = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(os.getenv("LLM_TIMEOUT", "60"))
        self.http_client = httpx.Client(
            timeout=self.timeout,
            trust_env=False,
        )
        self.client = OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            http_client=self.http_client,
        )

    def build_messages(self, message: str, history: list[ChatMessage]) -> list[dict[str, str]]:
        """Build the exact Qwen message list from user-visible chat history."""

        messages: list[dict[str, str]] = [
            {"role": item.role, "content": item.content}
            for item in history
            if item.role in {"user", "assistant"}
        ]
        messages.append({"role": "user", "content": message})
        return messages

    def chat(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        """Send only model and messages to the OpenAI-compatible chat endpoint."""

        resolved_model = model or self.model
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=messages,
        )
        return response.choices[0].message.content or ""


def pure_qwen_metadata() -> dict[str, object]:
    """Return public configuration metadata without exposing secrets."""

    return {
        "mode": "pure_qwen",
        "model": os.getenv("LLM_MODEL", "qwen3.8-max"),
        "base_url": os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        "api_key_configured": bool(os.getenv("DASHSCOPE_API_KEY", "")),
    }
