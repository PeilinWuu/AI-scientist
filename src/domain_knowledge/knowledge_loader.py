"""Load distilled domain principles from a small YAML file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_domain_principles(
    path: str | Path = "knowledge/distilled/domain_principles.yaml",
) -> list[dict[str, Any]]:
    """Load distilled principles; missing or malformed files return an empty list."""

    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]
