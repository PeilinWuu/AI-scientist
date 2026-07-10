"""Versioned artifact storage for structured research products."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from src.ai_scientist.schemas import ArtifactRecord, new_id, utc_now


class ArtifactStore:
    """Write safe structured artifacts without prompts or credentials."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root)

    def save_json(
        self,
        project_id: str,
        artifact_type: str,
        content: Any,
        created_by: str,
    ) -> ArtifactRecord:
        artifact_id = new_id("artifact")
        directory = self.project_root / project_id / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{artifact_id}_{artifact_type}.json"
        payload = json.dumps(content, ensure_ascii=False, indent=2, default=str)
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        if api_key:
            payload = payload.replace(api_key, "[REDACTED_API_KEY]")
        path = directory / filename
        path.write_text(payload, encoding="utf-8")
        checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return ArtifactRecord(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            filename=filename,
            created_by=created_by,
            created_at=utc_now(),
            checksum=checksum,
        )

    def list(self, project_id: str) -> list[Path]:
        directory = self.project_root / project_id / "artifacts"
        return sorted(directory.glob("*")) if directory.exists() else []
