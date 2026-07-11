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
        version = self._next_version(directory, artifact_type, "json")
        filename = f"{artifact_type}_v{version}.json"
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
            version=version,
        )

    def save_named_json(
        self,
        project_id: str,
        filename: str,
        artifact_type: str,
        content: Any,
        created_by: str,
    ) -> ArtifactRecord:
        payload = json.dumps(content, ensure_ascii=False, indent=2, default=str)
        return self._save_named(project_id, filename, artifact_type, payload, created_by)

    def save_named_text(
        self,
        project_id: str,
        filename: str,
        artifact_type: str,
        content: str,
        created_by: str,
    ) -> ArtifactRecord:
        return self._save_named(project_id, filename, artifact_type, content, created_by)

    def list(self, project_id: str) -> list[Path]:
        directory = self.project_root / project_id / "artifacts"
        return sorted(directory.glob("*")) if directory.exists() else []

    def _save_named(
        self,
        project_id: str,
        filename: str,
        artifact_type: str,
        payload: str,
        created_by: str,
    ) -> ArtifactRecord:
        artifact_id = new_id("artifact")
        directory = self.project_root / project_id / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
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
            version=self._next_version(directory, artifact_type, filename.split(".")[-1]),
        )

    @staticmethod
    def _next_version(directory: Path, artifact_type: str, suffix: str) -> int:
        versions = []
        for path in directory.glob(f"{artifact_type}_v*.{suffix}"):
            stem = path.stem
            try:
                versions.append(int(stem.rsplit("_v", 1)[1]))
            except (IndexError, ValueError):
                continue
        return max(versions, default=0) + 1
