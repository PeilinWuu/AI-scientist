"""LLM provider factory and status helpers."""

from __future__ import annotations

import requests

from src.config import settings
from src.llm.base import LLMProvider
from src.llm.curl_qwen_provider import CurlQwenProvider
from src.llm.mock_provider import MockLLMProvider
from src.llm.qwen_provider import QwenProvider


class AutoQwenProvider(LLMProvider):
    """Try SDK, then requests, then curl for real Qwen access."""

    def __init__(self) -> None:
        self.model = settings.llm_model
        self.base_url = settings.llm_base_url.rstrip("/")
        self.actual_transport = "auto"
        self._last_provider: LLMProvider | None = None

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        errors: list[str] = []
        try:
            provider = QwenProvider()
            text = provider.generate(system_prompt, user_prompt)
            self.actual_transport = "openai_sdk"
            self._last_provider = provider
            return text
        except Exception as exc:  # noqa: BLE001 - auto transport should try next real path.
            errors.append(f"openai_sdk: {type(exc).__name__}: {exc}")

        try:
            text = self._generate_with_requests(system_prompt, user_prompt)
            self.actual_transport = "requests"
            self._last_provider = None
            return text
        except Exception as exc:  # noqa: BLE001 - auto transport should try curl next.
            errors.append(f"requests: {type(exc).__name__}: {exc}")

        try:
            provider = CurlQwenProvider()
            text = provider.generate(system_prompt, user_prompt)
            self.actual_transport = "curl"
            self._last_provider = provider
            return text
        except Exception as exc:  # noqa: BLE001 - raise one clear aggregate failure.
            errors.append(f"curl: {type(exc).__name__}: {exc}")

        raise RuntimeError("All real Qwen transports failed. " + " | ".join(errors))

    def metadata(self) -> dict:
        if self._last_provider:
            return self._last_provider.metadata()
        return {
            "provider": "qwen",
            "transport": self.actual_transport,
            "model": self.model,
            "base_url": self.base_url,
            "is_mock": False,
        }

    def _generate_with_requests(self, system_prompt: str, user_prompt: str) -> str:
        if not settings.dashscope_api_key:
            raise ValueError("DASHSCOPE_API_KEY is required when LLM_PROVIDER=qwen.")
        response = requests.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
            },
            headers={
                "Authorization": f"Bearer {settings.dashscope_api_key}",
                "Content-Type": "application/json",
                "User-Agent": "FlowScientist-Loop/0.1",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            error = data["error"] or {}
            raise RuntimeError(
                f"Qwen API error {error.get('code', 'unknown_code')}: "
                f"{error.get('message', 'Unknown Qwen API error')}"
            )
        return data["choices"][0]["message"]["content"]


def get_llm_provider() -> LLMProvider:
    """Create the configured LLM provider."""

    if settings.llm_provider == "mock":
        if settings.qwen_require_real:
            raise RuntimeError("Real Qwen is required. Mock fallback is disabled.")
        return MockLLMProvider()

    if settings.llm_provider == "qwen":
        if settings.qwen_transport == "curl":
            return CurlQwenProvider()
        if settings.qwen_transport == "auto":
            return AutoQwenProvider()
        if settings.qwen_transport in {"openai", "openai_sdk", "sdk"}:
            return QwenProvider()
        raise ValueError(f"Unsupported QWEN_TRANSPORT={settings.qwen_transport}")

    if settings.qwen_require_real:
        raise RuntimeError("Real Qwen is required. Mock fallback is disabled.")
    return MockLLMProvider()


def get_llm_status() -> dict:
    """Return actual configured LLM provider status without making a network call."""

    try:
        provider = get_llm_provider()
        metadata = provider.metadata()
        return {
            "llm_provider": metadata.get("provider", "unknown"),
            "llm_model": metadata.get("model", settings.llm_model),
            "llm_transport": metadata.get("transport", "unknown"),
            "llm_base_url": metadata.get("base_url", settings.llm_base_url),
            "mock_mode": bool(metadata.get("is_mock", True)),
            "qwen_require_real": settings.qwen_require_real,
            "status_error": None,
        }
    except Exception as exc:  # noqa: BLE001 - status should explain configuration errors.
        return {
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "llm_transport": "none" if settings.llm_provider != "qwen" else settings.qwen_transport,
            "llm_base_url": settings.llm_base_url,
            "mock_mode": settings.llm_provider != "qwen",
            "qwen_require_real": settings.qwen_require_real,
            "status_error": str(exc),
        }
