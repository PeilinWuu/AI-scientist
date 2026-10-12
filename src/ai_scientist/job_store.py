"""Persistent job records for asynchronous AI Scientist stages."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
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
    run_mode: str = "single_stage"
    pause_requested: bool = False
    progress: dict[str, Any] = Field(default_factory=dict)
    model_updates: list[dict[str, Any]] = Field(default_factory=list)


class ResearchJobStore:
    """Store one JSON file per asynchronous stage job."""

    def __init__(self, projects_root: str | Path) -> None:
        self.projects_root = Path(projects_root)
        self._update_lock = threading.RLock()

    def update(self, project_id: str, job_id: str, change) -> ResearchJob:
        """Serialize live output and cooperative pause writes within this API worker."""
        with self._update_lock:
            job = self.load(project_id, job_id)
            change(job)
            self.save(job)
            return job

    def record_model_progress(self, project_id: str, job_id: str, event: dict) -> None:
        # Accept public content only: never persist provider hidden reasoning or prompts.
        allowed = {'call_id', 'agent_name', 'model', 'status', 'timestamp', 'public_response',
                   'human_input_request', 'receipt', 'provider_responses', 'token_usage',
                   'elapsed_seconds', 'received_chunks', 'output_chars', 'figures', 'message_kind'}
        public = {key: value for key, value in event.items() if key in allowed}
        if 'figures' in public:
            from src.ai_scientist.figure_refs import clean_figure_refs
            public['figures'] = clean_figure_refs(public['figures'])
        receipt_fields = {'requested_model', 'response_model', 'response_id', 'request_id', 'status',
                          'first_chunk_seconds', 'first_content_seconds', 'total_seconds', 'output_chars'}
        for key in ('receipt',):
            if isinstance(public.get(key), dict):
                public[key] = {k: v for k, v in public[key].items() if k in receipt_fields}
            elif key in public:
                public.pop(key)
        if 'provider_responses' in public:
            public['provider_responses'] = [
                {k: v for k, v in value.items() if k in receipt_fields}
                for value in public['provider_responses'] if isinstance(value, dict)]
        if 'token_usage' in public:
            public['token_usage'] = {k: v for k, v in public['token_usage'].items()
                                     if k in {'prompt_tokens', 'completion_tokens', 'total_tokens'}
                                     and isinstance(v, int)}
        def change(job):
            previous = job.progress if job.progress.get('call_id') == public.get('call_id') else {}
            job.progress = {**previous, **public, 'phase': job.phase}
            receipts = public.get('provider_responses') or []
            if public.get('status') in {'completed', 'failure'} and receipts:
                durations = [r.get('total_seconds') for r in receipts]
                if all(isinstance(d, (int, float)) for d in durations):
                    job.progress['elapsed_seconds'] = round(sum(durations), 3)
            call_id = public.get('call_id')
            existing = next((item for item in job.model_updates if item.get('call_id') == call_id), None)
            if existing is not None:
                existing.update(public)
            else:
                job.model_updates.append({**public, 'phase': job.phase})
        self.update(project_id, job_id, change)

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
        # Redact scalar values before serializing. Applying text regexes to the
        # JSON document itself can consume its closing quotes and corrupt a job
        # as soon as a streamed sentence ends with e.g. "api_key=...".
        payload = json.dumps(_sanitize_values(job.model_dump(mode='json')), ensure_ascii=False, indent=2)
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

    def recover_orphaned_jobs(self) -> list[ResearchJob]:
        """Fail jobs whose in-process worker disappeared during an API restart.

        This is called once while the application module starts, before the new
        process can accept requests or create worker threads. It must not be
        called as a periodic sweep because that could interrupt current jobs.
        """

        recovered: list[ResearchJob] = []
        for path in sorted(self.projects_root.glob("*/jobs/*.json")):
            try:
                job = ResearchJob.model_validate_json(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError, OSError):
                continue
            if job.status not in {"queued", "running"}:
                continue
            job.status = "failed"
            job.finished_at = utc_now()
            job.error = {
                "error_type": "worker_restarted",
                "error_message": (
                    "The API worker restarted before this stage job completed; "
                    "the project remains at its last persisted phase and can be retried."
                ),
                "failure_category": "interrupted",
                "stage": job.phase,
            }
            self.save(job)
            recovered.append(job)
        return recovered


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


def _sanitize_values(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, dict):
        return {key: _sanitize_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_values(item) for item in value]
    return value


def _sanitize_text(text: str) -> str:
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    sanitized = text.replace(api_key, "[REDACTED_API_KEY]") if api_key else text
    sanitized = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;}]+",
        r"\1[REDACTED]",
        sanitized,
    )
    sanitized = re.sub(
        r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;}]+",
        r"\1[REDACTED]",
        sanitized,
    )
    return sanitized
