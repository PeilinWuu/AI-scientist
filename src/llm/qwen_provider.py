"""Qwen/DashScope provider using the official OpenAI SDK."""

from __future__ import annotations

from openai import OpenAI

from src.config import settings
from src.llm.base import LLMProvider


class QwenProvider(LLMProvider):
    """Thin Qwen provider. Secrets are read only from environment variables."""

    def __init__(self) -> None:
        if not settings.dashscope_api_key:
            raise ValueError("DASHSCOPE_API_KEY is required when LLM_PROVIDER=qwen.")
        self.api_key = settings.dashscope_api_key
        self.model = settings.llm_model
        self.base_url = settings.llm_base_url.rstrip("/")
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            timeout=30,
        )
        return response.choices[0].message.content or ""

    def metadata(self) -> dict:
        return {
            "provider": "qwen",
            "transport": "openai_sdk",
            "model": self.model,
            "base_url": self.base_url,
            "is_mock": False,
        }
