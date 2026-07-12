"""Shared model-name validation helpers."""

from __future__ import annotations


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
