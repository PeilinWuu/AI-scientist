"""Centralized UTC-to-local display helpers for user interfaces."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def ui_timezone() -> ZoneInfo:
    """Return the configured UI timezone, falling back to Asia/Shanghai."""

    name = os.getenv("UI_TIMEZONE", "Asia/Shanghai")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def parse_datetime(value: datetime | str | None) -> datetime | None:
    """Parse an ISO timestamp and treat legacy naive values as UTC."""

    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else _parse_iso(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_local_time(value: datetime | str | None) -> str:
    parsed = parse_datetime(value)
    return parsed.astimezone(ui_timezone()).strftime("%H:%M") if parsed else "--:--"


def format_local_datetime(value: datetime | str | None) -> str:
    parsed = parse_datetime(value)
    return parsed.astimezone(ui_timezone()).strftime("%Y-%m-%d %H:%M:%S %Z") if parsed else "--"


def format_utc_datetime(value: datetime | str | None) -> str:
    parsed = parse_datetime(value)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if parsed else "--"


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
