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

    def generate(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        extra_body = _build_qwen_extra_body(kwargs)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            timeout=30,
            extra_body=extra_body or None,
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


def _build_qwen_extra_body(options: dict) -> dict:
    extra_body: dict = {}
    if options.get("enable_search"):
        extra_body["enable_search"] = True
        search_options = dict(options.get("search_options") or {})
        if settings.qwen_search_strategy and "search_strategy" not in search_options:
            search_options["search_strategy"] = settings.qwen_search_strategy
        if "forced_search" not in search_options:
            search_options["forced_search"] = True
        elif settings.qwen_search_force:
            search_options["forced_search"] = True
        if settings.qwen_search_freshness is not None and "freshness" not in search_options:
            search_options["freshness"] = settings.qwen_search_freshness
        if settings.qwen_search_assigned_sites and "assigned_site_list" not in search_options:
            search_options["assigned_site_list"] = settings.qwen_search_assigned_sites
        if search_options:
            extra_body["search_options"] = search_options
    return extra_body
