"""Persistent job records for asynchronous AI Scientist stages."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.ai_scientist.schemas import new_id, utc_now


JobStatus = Literal["queued", "running", "completed", "failed"]


class ResearchJob(BaseModel):
    job_id: str = Field(default_factory=lambda: new_id("job"))
    project_id: str
    phase: str
    status: JobStatus = "queued"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


class ResearchJobStore:
    """Store one JSON file per asynchronous stage job."""

    def __init__(self, projects_root: str | Path) -> None:
        self.projects_root = Path(projects_root)

    def jobs_dir(self, project_id: str) -> Path:
        path = self.projects_root / project_id / "jobs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def job_path(self, project_id: str, job_id: str) -> Path:
        return self.jobs_dir(project_id) / f"{job_id}.json"

    def create(self, project_id: str, phase: str) -> ResearchJob:
        job = ResearchJob(project_id=project_id, phase=phase)
        self.save(job)
        return job

    def save(self, job: ResearchJob) -> Path:
        directory = self.jobs_dir(job.project_id)
        target = directory / f"{job.job_id}.json"
        payload = _sanitize_text(job.model_dump_json(indent=2))
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=directory,
                prefix=f"{job.job_id}_",
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

    def load(self, project_id: str, job_id: str) -> ResearchJob:
        path = self.job_path(project_id, job_id)
        if not path.exists():
            raise FileNotFoundError(f"Research job not found: {job_id}")
        return ResearchJob.model_validate_json(path.read_text(encoding="utf-8"))

    def load_any(self, job_id: str) -> ResearchJob:
        for path in self.projects_root.glob(f"*/jobs/{job_id}.json"):
            return ResearchJob.model_validate_json(path.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"Research job not found: {job_id}")

    def active_step_job(self, project_id: str) -> ResearchJob | None:
        for path in sorted(self.jobs_dir(project_id).glob("*.json")):
            try:
                job = ResearchJob.model_validate_json(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                continue
            if job.status in {"queued", "running"}:
                return job
        return None


def fail_job(job: ResearchJob, exc: Exception) -> ResearchJob:
    job.status = "failed"
    job.finished_at = utc_now()
    job.error = {
        "error_type": type(exc).__name__,
        "error_message": _sanitize_text(str(exc)),
        "stage": getattr(exc, "stage", None),
        "stage_substep": getattr(exc, "substep", None),
        "failing_component": getattr(exc, "failing_component", None),
        "failure_category": getattr(exc, "failure_category", None),
        "artifact_type": getattr(exc, "artifact_type", None),
        "cause_type": getattr(exc, "cause_type", None),
        "cause_message": _sanitize_text(str(getattr(exc, "cause_message", "") or "")),
        "validation_errors": getattr(exc, "validation_errors", []),
    }
    return job


def _sanitize_text(text: str) -> str:
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    return text.replace(api_key, "[REDACTED_API_KEY]") if api_key else text
