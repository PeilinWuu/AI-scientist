"""Atomic persistence for AI Scientist projects and append-only events."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from src.ai_scientist.exceptions import ProjectNotFoundError
from src.ai_scientist.schemas import ResearchEvent, ResearchProject, utc_now


class ProjectStore:
    """Persist projects under one directory per research project."""

    def __init__(self, root: str | Path | None = None) -> None:
        configured = root or os.getenv("AI_SCIENTIST_PROJECTS_DIR", "data/research_projects")
        self.root = Path(configured)
        self.root.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        return self.root / project_id

    def save(self, project: ResearchProject) -> Path:
        """Atomically replace project.json after validating the project schema."""

        project.updated_at = utc_now()
        directory = self.project_dir(project.project_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "project.json"
        payload = _sanitize_text(project.model_dump_json(indent=2))
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=directory,
                prefix="project_",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.replace(temp_path, target)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()
        return target

    def load(self, project_id: str) -> ResearchProject:
        path = self.project_dir(project_id) / "project.json"
        if not path.exists():
            raise ProjectNotFoundError(f"Research project not found: {project_id}")
        return ResearchProject.model_validate_json(path.read_text(encoding="utf-8"))

    def append_event(self, event: ResearchEvent) -> Path:
        directory = self.project_dir(event.project_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "events.jsonl"
        line = _sanitize_text(event.model_dump_json())
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return path

    def list_events(self, project_id: str) -> list[ResearchEvent]:
        path = self.project_dir(project_id) / "events.jsonl"
        if not path.exists():
            return []
        return [
            ResearchEvent.model_validate(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def _sanitize_text(text: str) -> str:
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    return text.replace(api_key, "[REDACTED_API_KEY]") if api_key else text
