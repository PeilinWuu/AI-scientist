"""LLM call auditing helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.config import settings
from src.llm.base import LLMProvider
from src.utils.io import ensure_dir, write_json


@dataclass
class LLMCallRecord:
    """One recorded LLM call result."""

    raw_response: str
    response_sha256: str
    evidence: dict[str, Any]


class LLMCallRecorder:
    """Save every real or mock LLM call into a run-local audit folder."""

    def __init__(self, run_dir: Path, provider: LLMProvider) -> None:
        self.run_dir = run_dir
        self.calls_dir = run_dir / "llm_calls"
        ensure_dir(self.calls_dir)
        self.provider = provider
        self.sequence = 0

    def call(
        self,
        agent: str,
        system_prompt: str,
        user_prompt: str,
    ) -> LLMCallRecord:
        """Call the provider, save request/response files, and return evidence."""

        metadata = self.provider.metadata()
        if settings.qwen_require_real and metadata.get("is_mock", True):
            raise RuntimeError("Real Qwen is required. Mock fallback is disabled.")

        self.sequence += 1
        slug = _agent_slug(agent)
        prefix = f"{self.sequence:03d}_{slug}"
        full_prompt = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}"
        request_payload = {
            "agent": agent,
            "provider": metadata.get("provider"),
            "transport": metadata.get("transport"),
            "model": metadata.get("model"),
            "is_mock": bool(metadata.get("is_mock", True)),
            "prompt": full_prompt,
            "prompt_sha256": _hash(full_prompt),
            "timestamp": _timestamp(),
        }
        write_json(self.calls_dir / f"{prefix}_request.json", request_payload)

        raw_response = self.provider.generate(system_prompt, user_prompt) or ""
        if settings.qwen_require_real and not raw_response.strip():
            raise RuntimeError(f"{agent} returned an empty real Qwen response.")

        response_sha = _hash(raw_response)
        response_payload = {
            "agent": agent,
            "provider": metadata.get("provider"),
            "transport": metadata.get("transport"),
            "model": metadata.get("model"),
            "is_mock": bool(metadata.get("is_mock", True)),
            "raw_response": raw_response,
            "response_sha256": response_sha,
            "timestamp": _timestamp(),
        }
        write_json(self.calls_dir / f"{prefix}_response.json", response_payload)

        evidence = {
            "llm_used": True,
            "llm_provider": metadata.get("provider"),
            "llm_transport": metadata.get("transport"),
            "llm_model": metadata.get("model"),
            "llm_response_excerpt": raw_response[:300],
            "llm_response_sha256": response_sha,
        }
        return LLMCallRecord(
            raw_response=raw_response,
            response_sha256=response_sha,
            evidence=evidence,
        )

    def count(self) -> int:
        """Return the number of completed response files."""

        return len(list(self.calls_dir.glob("*_response.json")))

    def last_response_excerpt(self) -> str:
        """Return the latest raw response excerpt."""

        responses = sorted(self.calls_dir.glob("*_response.json"))
        if not responses:
            return ""
        data = json.loads(responses[-1].read_text(encoding="utf-8"))
        return str(data.get("raw_response", ""))[:300]


def parse_llm_json(raw_response: str, agent: str) -> dict[str, Any]:
    """Parse strict JSON from an LLM response, with one simple repair pass."""

    raw_response = raw_response or ""
    text = raw_response.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                if settings.qwen_require_real:
                    raise RuntimeError(
                        f"{agent} returned invalid JSON after repair attempt: {exc}. "
                        f"Raw response excerpt: {raw_response[:300]}"
                    ) from exc
        if settings.qwen_require_real:
            raise RuntimeError(
                f"{agent} returned invalid JSON and no object could be extracted. "
                f"Raw response excerpt: {raw_response[:300]}"
            )
    return {}


def evidence_from_raw(provider: LLMProvider, raw_response: str) -> dict[str, Any]:
    """Build evidence for legacy/mock fallback branches."""

    raw_response = raw_response or ""
    metadata = provider.metadata()
    return {
        "llm_used": bool(raw_response),
        "llm_provider": metadata.get("provider"),
        "llm_transport": metadata.get("transport"),
        "llm_model": metadata.get("model"),
        "llm_response_excerpt": raw_response[:300],
        "llm_response_sha256": _hash(raw_response) if raw_response else "",
    }


def _hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _agent_slug(agent: str) -> str:
    slug = agent
    if slug.endswith("Agent"):
        slug = slug[:-5]
    chars = []
    for index, char in enumerate(slug):
        if char.isupper() and index > 0:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)
