"""Audited restricted Python analysis in an isolated child process.

This is a defence-in-depth analysis runner, not an OS/container security boundary.
User code receives an in-memory dataset and a small scientific namespace. Imports,
file APIs, networking, subprocesses and introspection are rejected before execution.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil

from src.ai_scientist.schemas import new_id, utc_now


class ControlledPythonSandbox:
    """Run restricted analysis code with time, memory and process limits."""

    MAX_CODE_CHARS = 20_000
    MAX_CAPTURE_CHARS = 16_000

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65_536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _redact(value: str) -> str:
        redacted = value
        for key, secret in os.environ.items():
            if secret and len(secret) >= 8 and any(
                marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD")
            ):
                redacted = redacted.replace(secret, f"[REDACTED_{key.upper()}]")
        return redacted

    @classmethod
    def _redact_value(cls, value: Any) -> Any:
        if isinstance(value, str):
            return cls._redact(value)
        if isinstance(value, dict):
            return {str(key): cls._redact_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._redact_value(item) for item in value]
        return value

    @classmethod
    def _redact_text_artifact(cls, path: Path) -> None:
        if path.suffix.lower() not in {".json", ".csv", ".txt"} or path.stat().st_size > 2_000_000:
            return
        content = path.read_text(encoding="utf-8", errors="replace")
        redacted = cls._redact(content)
        if redacted != content:
            path.write_text(redacted, encoding="utf-8")

    @staticmethod
    def _terminate_tree(process: subprocess.Popen[str]) -> None:
        try:
            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
            for child in children:
                child.kill()
            parent.kill()
            psutil.wait_procs([parent, *children], timeout=2)
        except (psutil.Error, ProcessLookupError):
            process.kill()

    def execute(
        self,
        *,
        code: str,
        dataset_path: str | Path,
        timeout_seconds: int = 15,
        memory_limit_mb: int = 1024,
        seed: int = 0,
    ) -> dict[str, Any]:
        started_at = utc_now()
        started_clock = time.perf_counter()
        run_id = new_id("python_sandbox")
        timeout_seconds = max(1, min(int(timeout_seconds), 30))
        memory_limit_mb = max(256, min(int(memory_limit_mb), 1536))
        dataset = Path(dataset_path).resolve()
        try:
            dataset.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError("dataset path escapes the project boundary") from exc
        if not dataset.is_file():
            raise FileNotFoundError("registered dataset file does not exist")
        if len(code) > self.MAX_CODE_CHARS:
            raise ValueError(f"code exceeds {self.MAX_CODE_CHARS} characters")

        # Keep the physical directory compact for Windows MAX_PATH while the
        # full run id remains in the audit record.
        run_root = self.project_root / "controlled_python" / f"py_{run_id.rsplit('_', 1)[-1][:8]}"
        workspace = run_root / "workspace"
        workspace.mkdir(parents=True, exist_ok=False)
        input_path = workspace / f"input{dataset.suffix.lower()}"
        shutil.copy2(dataset, input_path)
        request_path = workspace / "request.json"
        result_path = workspace / "worker_result.json"
        request_path.write_text(
            json.dumps({
                "code": code,
                "dataset_filename": input_path.name,
                "seed": int(seed),
                "max_output_chars": self.MAX_CAPTURE_CHARS,
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        worker = Path(__file__).with_name("controlled_python_worker.py").resolve()
        safe_env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
        safe_env.update({
            "TEMP": str(workspace), "TMP": str(workspace),
            "HOME": str(workspace), "USERPROFILE": str(workspace),
            "PYTHONNOUSERSITE": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1", "HTTP_PROXY": "",
            "HTTPS_PROXY": "", "ALL_PROXY": "", "NO_PROXY": "*",
        })
        process = subprocess.Popen(
            [sys.executable, "-I", str(worker), str(request_path), str(result_path)],
            cwd=workspace,
            env=safe_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        forced_status: str | None = None
        forced_error: str | None = None
        peak_memory_mb = 0.0
        while process.poll() is None:
            elapsed = time.perf_counter() - started_clock
            try:
                observed = psutil.Process(process.pid)
                children = observed.children(recursive=True)
                if children:
                    forced_status, forced_error = "rejected", "child process creation is not permitted"
                    self._terminate_tree(process)
                    break
                peak_memory_mb = max(peak_memory_mb, observed.memory_info().rss / (1024 * 1024))
                if peak_memory_mb > memory_limit_mb:
                    forced_status = "resource_exceeded"
                    forced_error = f"memory limit exceeded ({memory_limit_mb} MB)"
                    self._terminate_tree(process)
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            if elapsed > timeout_seconds:
                forced_status = "timeout"
                forced_error = f"execution exceeded {timeout_seconds} seconds"
                self._terminate_tree(process)
                break
            time.sleep(0.05)

        stdout, stderr = process.communicate(timeout=3)
        worker_result: dict[str, Any] = {}
        if result_path.is_file():
            try:
                worker_result = json.loads(result_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                worker_result = {}
        request_path.unlink(missing_ok=True)
        result_path.unlink(missing_ok=True)
        status = forced_status or worker_result.get("status") or ("success" if process.returncode == 0 else "failed")
        error = forced_error or worker_result.get("error")
        artifacts: list[dict[str, Any]] = []
        for raw_path in worker_result.get("artifacts") or []:
            candidate = (workspace / str(raw_path)).resolve()
            try:
                relative = candidate.relative_to(self.project_root)
            except ValueError:
                continue
            if candidate.is_file():
                self._redact_text_artifact(candidate)
                artifacts.append({
                    "relative_path": relative.as_posix(),
                    "checksum_sha256": self._sha256(candidate),
                    "size_bytes": candidate.stat().st_size,
                })
        return {
            "run_id": run_id, "status": status,
            "started_at": started_at.isoformat(), "finished_at": utc_now().isoformat(),
            "duration_ms": max(0, round((time.perf_counter() - started_clock) * 1000)),
            "code": self._redact(code),
            "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
            "dataset_relative_path": dataset.relative_to(self.project_root).as_posix(),
            "dataset_sha256": self._sha256(dataset), "seed": int(seed),
            "timeout_seconds": timeout_seconds, "memory_limit_mb": memory_limit_mb,
            "peak_memory_mb": round(peak_memory_mb, 2),
            "isolation": {
                "mode": "restricted_subprocess_ast_guard", "container_or_vm": False,
                "imports_allowed": False, "network_allowed": False,
                "filesystem_api_allowed": False, "child_processes_allowed": False,
                "parent_environment_inherited": False,
                "security_boundary_note": "Defence in depth; not equivalent to a container, VM, or separate OS account.",
            },
            "available_names": ["data", "np", "pd"],
            "result": self._redact_value(worker_result.get("result")),
            "stdout": self._redact(str(worker_result.get("stdout") or stdout))[: self.MAX_CAPTURE_CHARS],
            "stderr": self._redact(str(worker_result.get("stderr") or stderr))[: self.MAX_CAPTURE_CHARS],
            "error_type": worker_result.get("error_type") or ("SandboxLimitError" if forced_error else None),
            "error": self._redact(str(error))[:2000] if error else None,
            "artifacts": artifacts, "worker_return_code": process.returncode,
        }
