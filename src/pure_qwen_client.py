"""Minimal shared OpenAI-compatible Qwen client."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import OpenAI

from src.model_utils import model_max_retries

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
            max_retries=model_max_retries(),
        )

    def chat(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        """Send only model and messages to the OpenAI-compatible chat endpoint."""

        resolved_model = model or self.model
        response = self.client.chat.completions.create(
            model=resolved_model,
            messages=messages,
        )
        return response.choices[0].message.content or ""
