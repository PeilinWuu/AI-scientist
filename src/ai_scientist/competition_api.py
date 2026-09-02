"""FastAPI routes for the shortest Competition 1B judging path."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from src.ai_scientist.competition_runtime import CompetitionRuntime
from src.ai_scientist.competition_schemas import CompetitionRunState


router = APIRouter(prefix="/api/competition/1b", tags=["Competition 1B feedback loop"])


class CompetitionDemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"seed": 20260831}]})
    seed: int = Field(default=20260831, ge=0, le=2_147_483_647, description="Reproducible observation noise seed.")


def demo_root() -> Path:
    configured = os.getenv("AI_SCIENTIST_COMPETITION_DIR", "competition/1b")
    return Path(configured).resolve() / "api_demo"


@router.get("/readiness", summary="检查 Competition 1B 演示后端就绪状态", description="确认确定性演示输出目录可写；不调用 Qwen。")
def competition_readiness() -> dict:
    root = demo_root()
    return {
        "status": "ready",
        "execution_backend": "controlled_local_deterministic",
        "arbitrary_code_execution": False,
        "output_root": str(root),
        "output_root_writable": _writable(root),
    }


@router.post(
    "/demo/run",
    response_model=CompetitionRunState,
    summary="运行完整 Competition 1B 两轮反馈演示",
    description="运行 Round 1、反馈、Round 2 与 baseline；使用确定性运行时，不执行任意代码。",
    responses={422: {"description": "Invalid seed or request shape"}, 500: {"description": "Deterministic execution failed"}},
)
def run_competition_demo(request: CompetitionDemoRequest) -> CompetitionRunState:
    state = CompetitionRuntime(demo_root()).run_flagship(request.seed)
    if state.status == "failed":
        raise HTTPException(status_code=500, detail={"error": "competition_execution_failed", **state.comparison})
    return state


@router.get("/demo", response_model=CompetitionRunState, summary="读取最新 Competition 1B 演示状态")
def get_competition_demo() -> CompetitionRunState:
    path = demo_root() / "audit/run_state.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail={"error": "competition_demo_not_run"})
    return CompetitionRunState.model_validate_json(path.read_text(encoding="utf-8"))


@router.get("/demo/history", summary="读取演示审计事件时间线")
def get_competition_history() -> list[dict]:
    path = demo_root() / "audit/event_log_excerpt.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail={"error": "competition_demo_not_run"})
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@router.get("/demo/artifacts", summary="列出演示产物及校验和")
def get_competition_artifacts() -> list[dict]:
    root = demo_root()
    if not (root / "audit/run_state.json").exists():
        raise HTTPException(status_code=404, detail={"error": "competition_demo_not_run"})
    return [
        {
            "relative_path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "checksum_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


@router.get("/demo/artifacts/{artifact_path:path}", summary="下载或查看一个演示产物")
def get_competition_artifact(artifact_path: str) -> FileResponse:
    root = demo_root().resolve()
    path = (root / artifact_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "artifact_path_outside_demo"}) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"error": "artifact_not_found"})
    return FileResponse(path)


@router.post("/demo/failure-cases", summary="运行受控拒绝与人工复核案例")
def run_competition_failure_cases() -> list[dict]:
    return CompetitionRuntime(demo_root()).run_failure_cases()


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        marker = path / ".readiness_check"
        marker.write_text("ok", encoding="utf-8")
        marker.unlink()
        return True
    except OSError:
        return False
