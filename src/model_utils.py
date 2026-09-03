"""Shared model-name validation helpers."""

from __future__ import annotations

import os


def model_max_retries() -> int:
    """Return the bounded SDK transport retry count (0-2, production default 1).

    OpenAI SDK transport retries cover transient connection/timeouts, 408/409,
    429 and 5xx responses, while non-recoverable 4xx responses are not retried.
    These are transport attempts inside one audited logical model call, so they
    do not inflate the research model-call budget or structured-repair count.
    """

    try:
        configured = int(os.getenv("AI_SCIENTIST_MODEL_MAX_RETRIES", "1"))
    except ValueError:
        configured = 1
    return min(2, max(0, configured))


def normalize_model_name(value: str | None) -> str | None:
    """Return a stripped model id or None, rejecting unsafe text."""

    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 128:
        raise ValueError("Model name must be 128 characters or fewer.")
    if any(char in normalized for char in "\r\n"):
        raise ValueError("Model name must not contain line breaks.")
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise ValueError("Model name must not contain control characters.")
    return normalized


def normalize_model_overrides(values: dict[str, str | None] | None) -> dict[str, str]:
    """Clean per-role model overrides, dropping empty fields."""

    result: dict[str, str] = {}
    for key, value in (values or {}).items():
        cleaned = normalize_model_name(value)
        if cleaned:
            result[str(key)] = cleaned
    return result
