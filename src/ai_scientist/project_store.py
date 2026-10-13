"""Atomic persistence for AI Scientist projects and append-only events."""

from __future__ import annotations

import json
import os
import tempfile
import re
import hashlib
import shutil
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

    def deletion_preview(self, project_id: str) -> dict:
        if not re.fullmatch(r'project_[a-zA-Z0-9]+', project_id):
            raise ValueError('Invalid project identifier')
        root = self.root.resolve()
        target = root / project_id
        if target.is_symlink() or target.resolve().parent != root:
            raise ValueError('Refusing deletion outside a direct project directory')
        if not (target / 'project.json').is_file():
            raise ProjectNotFoundError(f'Research project not found: {project_id}')
        inventory = []
        for path in sorted(target.rglob('*')):
            if path.is_symlink():
                raise ValueError('项目包含符号链接，请先人工检查；未删除任何文件。')
            if path.is_file():
                stat = path.stat()
                inventory.append([str(path.relative_to(target)), stat.st_size, stat.st_mtime_ns])
        project = self.load(project_id)
        token = hashlib.sha256(json.dumps([project_id, inventory]).encode()).hexdigest()
        return {'project_id': project_id, 'title': project.title, 'file_count': len(inventory),
                'size_bytes': sum(row[1] for row in inventory), 'confirmation_token': token}

    def delete_project(self, project_id: str, confirmation_token: str) -> dict:
        preview = self.deletion_preview(project_id)
        if preview['confirmation_token'] != confirmation_token:
            raise ValueError('项目文件已变化，请重新核对删除范围。')
        if not shutil.rmtree.avoids_symlink_attacks:
            raise ValueError('当前平台不支持安全目录删除，请人工处理。')
        # Only the validated direct child; never follow stored artifact paths.
        shutil.rmtree(self.root.resolve() / project_id)
        return {'deleted': True, 'project_id': project_id, 'file_count': preview['file_count']}

    def list_projects(self) -> list[dict]:
        """Small navigation records; never return prompts, credentials or results."""
        projects = []
        for path in self.root.glob('*/project.json'):
            if path.is_symlink() or path.parent.is_symlink():
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                title = data.get('title') or data.get('objective') or '未命名研究'
                projects.append({'project_id': path.parent.name, 'title': title[:160],
                                 'phase': data.get('phase'), 'updated_at': data.get('updated_at', '')})
            except (OSError, ValueError, TypeError):
                continue
        return sorted(projects, key=lambda p: p['updated_at'], reverse=True)

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
