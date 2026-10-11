"""FastAPI backend for AI Scientist and the Competition 1B demo."""

from __future__ import annotations

import os
import base64
import mimetypes
import threading
import time
import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse

from src.ai_scientist.job_store import ResearchJobStore, fail_job
from src.ai_scientist.model_progress import model_progress
from src.ai_scientist.continuous_research import continue_research
from src.ai_scientist.exceptions import ModelInputRequired
from src.ai_scientist.competition_api import router as competition_router
from src.ai_scientist.model_registry import ModelRegistry
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import (
    ApprovalRequest,
    ControlledPythonRunRequest,
    DeferApprovalRequest,
    EvidenceCreateRequest,
    HumanEditRequest,
    HumanSourceRequest,
    ProvideDataRequest,
    ResearchStartRequest,
    ResearchAssetUploadRequest,
    RevisionRequest,
    RevisionReviewDeferRequest,
    RevisionReviewSubmitRequest,
    SearchPlanReviewRequest,
    SourceSelectionRequest,
)
from src.pure_qwen_client import PureQwenClient
from src.search_qwen_client import SearchQwenClient
from src.model_utils import normalize_model_name
from src.ai_scientist.computation import ComputationService, enabled as computation_enabled
from src.ai_scientist.tools.computation_sandbox import runtime_status
from pydantic import BaseModel, Field
from src.ai_scientist.research_workspace import ResearchWorkspaceService, workspace_markdown


@asynccontextmanager
async def application_lifespan(application):
    # Recovery belongs to service startup, not module import. Importing API code
    # for tests or CLI utilities must never mark a live server's jobs orphaned.
    research_job_store.recover_orphaned_jobs()
    research_orchestrator.recover_revision_projects()
    yield


app = FastAPI(
    title="AI Scientist API",
    description="Scientific task planning, controlled execution, evidence research, and feedback iteration.",
    version="1.0.0",
    lifespan=application_lifespan,
)
app.include_router(competition_router)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

INLINE_ASSET_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".md": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".tsv": "text/tab-separated-values; charset=utf-8",
    ".json": "application/json",
    ".xml": "application/xml",
}

research_orchestrator = ResearchOrchestrator()
research_job_store = ResearchJobStore(research_orchestrator.store.root)
computation_service = ComputationService(research_orchestrator)
workspace_service = ResearchWorkspaceService(research_orchestrator)
computation_queue_lock = threading.Lock()
project_request_locks: dict[str, asyncio.Lock] = {}


@app.middleware('http')
async def serialize_project_mutations(request, call_next):
    """One API worker: prevent project edits from overwriting an active async run."""
    match = re.match(r'^/api/research/(project_[a-zA-Z0-9]+)/', request.url.path)
    if request.method not in {'POST', 'PATCH', 'DELETE'} or not match:
        return await call_next(request)
    project_id = match.group(1)
    if request.url.path.endswith('/workspace/pause'):
        return await call_next(request)
    async with project_request_locks.setdefault(project_id, asyncio.Lock()):
        active = research_job_store.active_step_job(project_id)
        if active:
            return JSONResponse(status_code=409, content={'detail': {
                'error': 'project_step_already_running', 'job_id': active.job_id, 'project_id': project_id}})
        return await call_next(request)


def _controlled_python_enabled() -> bool:
    return os.getenv("AI_SCIENTIST_ENABLE_CONTROLLED_PYTHON", "0").lower() in {
        "1", "true", "yes", "on",
    }


@app.get("/health")
def health() -> dict:
    """Return public backend status."""

    return {
        "status": "ok",
        "product": "ai_scientist",
        "qwen_configured": bool(os.getenv("DASHSCOPE_API_KEY", "")),
        "controlled_python_sandbox_enabled": _controlled_python_enabled(),
        "general_computation_enabled": computation_enabled(),
        "general_computation_runtime": runtime_status() if computation_enabled() else {"available": False, "reason": "disabled"},
    }


@app.get("/api/config/models")
def model_config() -> dict:
    """Return public default model configuration without secrets."""

    return {"ai_scientist": ModelRegistry.public_defaults()}


@app.post("/api/models/test")
def model_connectivity_test(request: dict) -> dict:
    """Test one user-provided model id using a minimal Qwen call."""

    started = time.perf_counter()
    try:
        model = normalize_model_name(request.get("model"))
        mode = request.get("mode")
        if not model:
            raise ValueError("Model name is required.")
        if mode not in {"chat", "search"}:
            raise ValueError("mode must be 'chat' or 'search'.")
        if mode == "chat":
            PureQwenClient().chat(messages=[{"role": "user", "content": "Reply with OK."}], model=model)
        else:
            client = SearchQwenClient()
            client.client.responses.create(
                model=model,
                input="Search for the current date and reply briefly.",
                tools=[{"type": "web_search"}],
            )
        return {
            "status": "ok",
            "model": model,
            "mode": mode,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 - safe diagnostic endpoint.
        return {
            "status": "error",
            "model": _safe_request_model(request),
            "mode": str(request.get("mode") or ""),
            "error_category": _model_error_category(exc),
            "message": _model_error_message(exc),
        }


def _sanitize(message: str) -> str:
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    return message.replace(api_key, "[REDACTED_API_KEY]") if api_key else message


def _safe_request_model(request: dict) -> str:
    try:
        return normalize_model_name(request.get("model")) or ""
    except Exception:
        return ""


def _model_error_category(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(token in text for token in ["not found", "does not exist", "invalid model", "model_not_found"]):
        return "model_not_found"
    if any(token in text for token in ["permission", "not authorized", "forbidden", "access denied"]):
        return "permission_denied"
    if any(token in text for token in ["quota", "insufficient", "rate limit"]):
        return "quota_exhausted"
    if any(token in text for token in ["web_search", "tool", "unsupported"]):
        return "unsupported_tool"
    if any(token in text for token in ["timeout", "connection", "ssl", "network"]):
        return "network_error"
    return "provider_error"


def _model_error_message(exc: Exception) -> str:
    return {
        "model_not_found": "模型名称不可用，请确认该模型 ID 是否被当前百炼账号支持。",
        "permission_denied": "当前账号没有调用该模型的权限。",
        "quota_exhausted": "模型调用额度或速率限制可能已耗尽。",
        "unsupported_tool": "该模型可能不支持当前联网搜索工具。",
        "network_error": "网络或连接层调用失败，请稍后重试。",
        "provider_error": "模型服务返回错误，请检查模型名称和账号权限。",
    }[_model_error_category(exc)]


def _responses_endpoint_host() -> str:
    base_url = os.getenv("RESPONSES_BASE_URL") or os.getenv(
        "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    return urlparse(base_url).netloc


@app.post(
    "/api/research/start",
    summary="创建研究项目",
    description="创建持久化 AI Scientist 项目；此接口只创建项目，不自动消耗模型调用。",
    responses={
        200: {"description": "项目已创建", "content": {"application/json": {"example": {"project_id": "project_demo123", "phase": "QUESTION_INTAKE", "status": "created"}}}},
        422: {"description": "请求字段校验失败"},
    },
)
def research_start(request: ResearchStartRequest) -> dict:
    """Create a persisted AI Scientist project without running a model stage."""

    try:
        project = research_orchestrator.create_project(
            objective=request.objective,
            domain_hint=request.domain_hint,
            constraints_text=request.constraints_text,
            constraints=request.constraints,
            model_overrides=request.model_overrides,
            max_iterations=request.max_iterations,
            planning_only=request.planning_only,
            evidence_review_mode=request.evidence_review_mode,
            reproducibility_seed=request.reproducibility_seed,
            research_policy_version=request.research_policy_version,
        )
        return {
            "project_id": project.project_id,
            "phase": project.phase.value,
            "status": "created",
        }
    except Exception as exc:  # noqa: BLE001 - converted to a safe API detail.
        raise _research_http_error(exc) from exc


@app.get('/api/research-projects')
def research_project_index() -> list[dict]:
    return research_orchestrator.store.list_projects()


class ProjectTitleUpdate(BaseModel):
    title: str


@app.patch('/api/research/{project_id}/title')
def research_project_title(project_id: str, request: ProjectTitleUpdate) -> dict:
    if not request.title.strip() or len(request.title) > 160:
        raise HTTPException(422, 'Project name must contain 1–160 characters')
    # In-flight workers hold snapshots; avoid losing either research or rename.
    if research_job_store.active_step_job(project_id):
        raise HTTPException(409, 'Pause research before renaming the project')
    try:
        project = research_orchestrator.get_project(project_id)
        project.title = request.title.strip()
        research_orchestrator.store.save(project)
        return project.model_dump(mode='json')
    except Exception as exc:
        raise _research_http_error(exc) from exc


@app.get("/api/research/debug/evidence-model")
def research_debug_evidence_model() -> dict:
    """Return safe evidence researcher model resolution for new projects."""

    resolution = ModelRegistry().resolve_model("evidence_researcher")
    return {
        "role": "evidence_researcher",
        "override_present": False,
        "environment_model_configured": bool(resolution.environment_model),
        "fallback_model_configured": bool(resolution.fallback_model),
        "resolved_model": resolution.resolved_model,
        "resolution_source": resolution.resolution_source,
        "responses_base_url_host": _responses_endpoint_host(),
    }


@app.post("/api/research/debug/evidence-search-ping")
def research_debug_evidence_search_ping(request: dict) -> dict:
    """Run the same minimal evidence search path used by BACKGROUND_RESEARCH."""

    project_id = str(request.get("project_id") or "")
    query = str(request.get("query") or "").strip()
    if not project_id or not query:
        raise HTTPException(status_code=400, detail={"error_message": "project_id and query are required."})
    project = research_orchestrator.get_project(project_id)
    resolution = ModelRegistry(project.model_overrides).resolve_model("evidence_researcher")
    try:
        result = SearchQwenClient(timeout_env="AI_SCIENTIST_SEARCH_TIMEOUT").search(
            message=query,
            model=resolution.resolved_model,
            previous_response_id=None,
        )
        return {
            "status": "ok",
            "resolved_model": resolution.resolved_model,
            "response_id": result.get("response_id"),
            "request_id": result.get("request_id"),
            "search_used": result.get("search_used"),
            "summary": result.get("reply"),
        }
    except Exception as exc:  # noqa: BLE001 - diagnostic endpoint returns safe detail.
        return {
            "status": "error",
            "resolved_model": resolution.resolved_model,
            "status_code": getattr(exc, "status_code", None),
            "provider_error_code": getattr(exc, "provider_error_code", None),
            "provider_error_message": getattr(exc, "provider_error_message", None) or _sanitize(str(exc)),
            "request_id": getattr(exc, "request_id", None),
        }


@app.post("/api/research/debug/claim-mapping")
def research_debug_claim_mapping(request: dict) -> dict:
    """Dry-run claim-evidence mapping diagnostics without advancing project state."""

    project_id = str(request.get("project_id") or "")
    if not project_id:
        raise HTTPException(status_code=400, detail={"error_message": "project_id is required."})
    try:
        return research_orchestrator.debug_claim_mapping(project_id)
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.get(
    "/api/research/{project_id}",
    summary="查询研究项目状态与结果",
    description="返回项目阶段、证据、主张、审计信息、产物和最终综合结果。",
    responses={404: {"description": "项目不存在"}},
)
def research_get(project_id: str) -> dict:
    """Return display-safe structured project state."""

    try:
        payload = research_orchestrator.get_project(project_id).model_dump(mode="json")
        active = research_job_store.active_step_job(project_id)
        if active:
            payload['active_job_id'] = active.job_id
        return payload
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/step")
def research_step(project_id: str) -> dict:
    """Run exactly one state-machine phase."""

    try:
        return research_orchestrator.run_next_step(project_id)
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post(
    "/api/research/{project_id}/step_async",
    status_code=status.HTTP_202_ACCEPTED,
    summary="异步运行一个研究阶段",
    description="推进一次状态机阶段并返回 job_id；评委可轮询 GET /api/research/jobs/{job_id}。",
    responses={
        202: {"description": "任务已排队", "content": {"application/json": {"example": {"job_id": "job_demo123", "project_id": "project_demo123", "phase": "QUESTION_INTAKE", "status": "queued"}}}},
        409: {"description": "项目已有运行中的阶段任务"},
        404: {"description": "项目不存在"},
    },
)
def research_step_async(project_id: str) -> dict:
    """Queue one state-machine phase and return immediately."""

    try:
        project = research_orchestrator.get_project(project_id)
        active_job = research_job_store.active_step_job(project_id)
        if active_job:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "project_step_already_running",
                    "job_id": active_job.job_id,
                    "project_id": project_id,
                },
            )
        job = research_job_store.create(project_id, project.phase.value)
        thread = threading.Thread(
            target=_run_research_job,
            args=(project_id, job.job_id),
            name=f"research-step-{job.job_id}",
            daemon=True,
        )
        thread.start()
        return {
            "job_id": job.job_id,
            "project_id": project_id,
            "phase": job.phase,
            "status": job.status,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post('/api/research/{project_id}/continue_async', status_code=202)
def research_continue_async(project_id: str):
    """Continue the fixed workflow, stopping at every existing authorization gate."""
    try:
        project = research_orchestrator.get_project(project_id)
        with computation_queue_lock:
            if research_job_store.active_step_job(project_id):
                raise HTTPException(409, 'Project already has an active job')
            job = research_job_store.create(project_id, project.phase.value)
            job.run_mode = 'until_human_gate'
            research_job_store.save(job)
            threading.Thread(target=_run_research_job, args=(project_id, job.job_id), daemon=True).start()
        return job.model_dump(mode='json')
    except Exception as exc:
        raise _research_http_error(exc) from exc


@app.post('/api/research/jobs/{job_id}/pause')
def research_pause_job(job_id: str):
    job = research_job_store.load_any(job_id)
    if job.status not in {'queued', 'running'}:
        raise HTTPException(409, 'This task is no longer running')
    job = research_job_store.update(job.project_id, job_id,
                                   lambda value: setattr(value, 'pause_requested', True))
    return {'job_id': job_id, 'pause_requested': job.pause_requested,
            'message': '将在当前阶段结束后暂停；已经发出的模型请求不被伪装为取消。'}


@app.get('/api/research/{project_id}/conversation')
def research_conversation(project_id: str):
    research_orchestrator.get_project(project_id)
    messages = []
    for path in sorted(research_job_store.jobs_dir(project_id).glob('*.json')):
        job = research_job_store.load(project_id, path.stem)
        for update in job.model_updates:
            if update.get('public_response') or update.get('human_input_request'):
                messages.append({**update, 'job_id': job.job_id})
    messages.sort(key=lambda item: (item.get('timestamp', ''), item.get('call_id', '')))
    return {'messages': messages}


class HumanDialogueReply(BaseModel):
    request_id: str
    response: str = Field(min_length=1, max_length=20000)


@app.post('/api/research/{project_id}/reply')
def research_reply(project_id: str, request: HumanDialogueReply):
    from src.ai_scientist.schemas import utc_now
    project = research_orchestrator.get_project(project_id)
    pending = project.human_dialogue.get('pending') or {}
    if pending.get('request_id') != request.request_id:
        raise HTTPException(409, 'Question changed or already answered; refresh first')
    if not request.response.strip():
        raise HTTPException(422, 'An answer is required')
    answer = {**pending, 'response': request.response, 'answered_at': utc_now().isoformat()}
    project.human_dialogue.setdefault('responses', []).append(answer)
    project.human_dialogue.pop('pending', None)
    # The answer is input, NOT an approval or proof that any research requirement is met.
    research_orchestrator.store.save(project)
    return {'status': 'received', 'request_id': request.request_id,
            'message': '回答已保存，下次研究调用将收到；尚不代表模型已处理或审批通过。'}


@app.get(
    "/api/research/jobs/{job_id}",
    summary="查询异步研究任务",
    description="查询一个阶段任务的 queued、running、completed 或 failed 状态。",
    responses={404: {"description": "任务不存在"}},
)
def research_job_status(job_id: str) -> dict:
    """Return a persisted asynchronous AI Scientist job record."""

    try:
        return research_job_store.load_any(job_id).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


def _run_research_job(project_id: str, job_id: str) -> None:
    job = research_job_store.load(project_id, job_id)
    job.status = "running"
    from src.ai_scientist.schemas import utc_now

    job.started_at = utc_now()
    research_job_store.save(job)
    try:
        def observe(event):
            research_job_store.record_model_progress(project_id, job_id, event)
        with model_progress(observe):
            if job.run_mode == 'until_human_gate':
                result = continue_research(
                    research_orchestrator, project_id, job_id,
                    should_pause=lambda: research_job_store.load(project_id, job_id).pause_requested,
                    on_stage=lambda phase: research_job_store.update(
                        project_id, job_id, lambda value: setattr(value, 'phase', phase)),
                )
            else:
                result = research_orchestrator.run_next_step(project_id, job_id=job_id)
        job = research_job_store.load(project_id, job_id)
        job.status = "completed"
        job.finished_at = utc_now()
        job.result = result
        research_job_store.save(job)
    except Exception as exc:  # noqa: BLE001 - safe detail is persisted for UI polling.
        job = research_job_store.load(project_id, job_id)
        job = fail_job(job, exc)
        research_job_store.save(job)


@app.post("/api/research/{project_id}/approve")
def research_approve(project_id: str, request: ApprovalRequest) -> dict:
    try:
        project = research_orchestrator.approve_project(
            project_id,
            acknowledged=request.acknowledged,
            expected_versions=request.expected_versions,
        )
        return {"project_id": project.project_id, "phase": project.phase.value, "status": "approved"}
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.get("/api/research/{project_id}/search-plan")
def research_search_plan(project_id: str) -> dict:
    try:
        project = research_orchestrator.get_project(project_id)
        plan = project.background_research_checkpoint.search_plan
        return {
            "phase": project.phase.value,
            "research_question": project.question.normalized_question if project.question else project.objective,
            "search_plan": plan.model_dump(mode="json") if plan else None,
        }
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


class StageAuthorizationRequest(BaseModel):
    scope_id: str
    allow_network: bool = False
    action_budget: int = Field(default=12, ge=1, le=40)
    max_model_calls: int | None = Field(default=None, ge=1, le=300)


class StageAdoptionRequest(BaseModel):
    scope_id: str
    proposal_id: str
    source_ids: list[str] = Field(default_factory=list, max_length=200)
    confirm_basis: bool = False


class StageFeedbackRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    regenerate: bool = False


def _stage_action(project_id, operation, **kwargs):
    from src.ai_scientist.background_agent import BackgroundAgent
    try:
        p = research_orchestrator.get_project(project_id)
        if operation != 'enable' and p.research_policy_version != 2:
            raise ValueError('Enable the stage agent explicitly before using it')
        getattr(BackgroundAgent(research_orchestrator), operation)(p, **kwargs)
        return p.model_dump(mode='json')
    except Exception as exc:
        raise _research_http_error(exc) from exc


@app.post('/api/research/{project_id}/background-agent/enable')
def enable_background_agent(project_id: str):
    return _stage_action(project_id, 'enable')


@app.post('/api/research/{project_id}/background-agent/authorize')
def authorize_background_agent(project_id: str, request: StageAuthorizationRequest):
    return _stage_action(project_id, 'authorize', **request.model_dump())


@app.post('/api/research/{project_id}/background-agent/adopt')
def adopt_background_agent(project_id: str, request: StageAdoptionRequest):
    return _stage_action(project_id, 'adopt', **request.model_dump())


@app.post('/api/research/{project_id}/background-agent/feedback')
def feedback_background_agent(project_id: str, request: StageFeedbackRequest):
    return _stage_action(project_id, 'feedback', **request.model_dump())


@app.post("/api/research/{project_id}/search-plan/approve")
def research_approve_search_plan(project_id: str, request: SearchPlanReviewRequest) -> dict:
    try:
        project = research_orchestrator.approve_search_plan(
            project_id, request.queries, request.auto_approve_future
        )
        return project.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/search-plan/regenerate")
def research_regenerate_search_plan(project_id: str) -> dict:
    try:
        return research_orchestrator.regenerate_search_plan(project_id).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.get("/api/research/{project_id}/source-candidates")
def research_source_candidates(project_id: str) -> dict:
    try:
        project = research_orchestrator.get_project(project_id)
        checkpoint = project.background_research_checkpoint
        latest = project.source_selection_snapshots[-1] if project.source_selection_snapshots else None
        return {
            "phase": project.phase.value,
            "review_mode": project.evidence_review_mode,
            "candidates": [item.model_dump(mode="json") for item in checkpoint.candidates],
            "latest_selection": latest.model_dump(mode="json") if latest else None,
            "formal_evidence_count": len(project.evidence),
        }
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/source-selection")
def research_source_selection(project_id: str, request: SourceSelectionRequest) -> dict:
    try:
        project = research_orchestrator.submit_source_selection(
            project_id, request.decisions, request.selection_note
        )
        return project.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/human-sources")
def research_human_sources(project_id: str, request: HumanSourceRequest) -> dict:
    try:
        return research_orchestrator.add_human_sources(project_id, request.entries).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/research-assets")
def research_upload_asset(project_id: str, request: ResearchAssetUploadRequest) -> dict:
    try:
        content = base64.b64decode(request.content_base64, validate=True)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail={"error": "upload_too_large", "max_bytes": MAX_UPLOAD_BYTES})
        project = research_orchestrator.register_research_asset(
            project_id,
            request.filename,
            request.content_type,
            content,
            purpose=request.purpose,
            description=request.description,
            upload_context=request.upload_context,
            asset_role=request.asset_role,
            research_round=request.research_round,
            source=request.source,
        )
        return {
            "project_id": project.project_id,
            "asset": project.research_assets[-1].model_dump(mode="json"),
            "message": (
                "文件已登记并完成本地解析。"
                if project.research_assets[-1].parsing_status == "parsed"
                else "文件已登记，但解析未完成；可在项目文件区查看原因并重试。"
            ),
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.delete("/api/research/{project_id}/research-assets/{asset_id}")
def research_delete_asset(project_id: str, asset_id: str) -> dict:
    """Delete an unused mistaken upload; used assets remain immutable for provenance."""

    try:
        project = research_orchestrator.delete_research_asset(project_id, asset_id)
        return {"project_id": project.project_id, "asset_id": asset_id, "status": "deleted"}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.get("/api/research/{project_id}/research-assets/{asset_id}")
def research_open_asset(project_id: str, asset_id: str) -> FileResponse:
    """Open a registered project file without exposing an arbitrary filesystem path."""

    try:
        asset, path = research_orchestrator.get_research_asset(project_id, asset_id)
        suffix = path.suffix.lower()
        media_type = INLINE_ASSET_MEDIA_TYPES.get(
            suffix, mimetypes.guess_type(asset.filename)[0] or "application/octet-stream"
        )
        encoded_name = quote(asset.filename, safe="")
        return FileResponse(
            path=path,
            media_type=media_type,
            headers={
                "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/research-assets/{asset_id}/parse")
def research_parse_asset(project_id: str, asset_id: str) -> dict:
    """Parse or retry parsing one registered project file."""

    try:
        project = research_orchestrator.parse_research_asset(project_id, asset_id)
        asset = next(item for item in project.research_assets if item.asset_id == asset_id)
        return {
            "project_id": project.project_id,
            "asset": asset.model_dump(mode="json"),
            "status": asset.parsing_status,
            "message": "文件解析完成。" if asset.parsing_status == "parsed" else "文件解析失败。",
        }
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.get("/api/research/{project_id}/review-package")
def research_review_package(project_id: str) -> dict:
    try:
        return research_orchestrator.get_review_package(project_id).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/defer-approval")
def research_defer_approval(project_id: str, request: DeferApprovalRequest) -> dict:
    try:
        project = research_orchestrator.defer_approval(project_id, request.reason)
        return {"project_id": project.project_id, "phase": project.phase.value, "status": "deferred"}
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/revise")
def research_revise(project_id: str, request: RevisionRequest) -> dict:
    try:
        project = research_orchestrator.request_revision(project_id, request.target, request.feedback)
        return {"project_id": project.project_id, "phase": project.phase.value, "status": "revision_requested"}
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.get("/api/research/{project_id}/revision-review")
def research_revision_review(project_id: str) -> dict:
    try:
        return research_orchestrator.get_revision_review(project_id)
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/revision-review/submit")
def research_revision_review_submit(project_id: str, request: RevisionReviewSubmitRequest) -> dict:
    try:
        project = research_orchestrator.submit_revision_review(project_id, request.decisions)
        plan = project.approved_revision_plans[-1]
        return {
            "project_id": project.project_id,
            "phase": project.phase.value,
            "status": "revision_plan_approved",
            "revision_plan": plan.model_dump(mode="json"),
        }
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/revision-review/defer")
def research_revision_review_defer(project_id: str, request: RevisionReviewDeferRequest) -> dict:
    try:
        project = research_orchestrator.defer_revision_review(project_id, request.reason)
        return {"project_id": project.project_id, "phase": project.phase.value, "status": "deferred"}
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/revision-review/resume-evidence-research")
def research_revision_review_resume_evidence_research(project_id: str) -> dict:
    try:
        project = research_orchestrator.resume_evidence_research(project_id)
        return {
            "project_id": project.project_id,
            "phase": project.phase.value,
            "status": "evidence_research_resumed",
        }
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/revision-review/cancel")
def research_revision_review_cancel(project_id: str) -> dict:
    try:
        project = research_orchestrator.cancel_project(project_id)
        return {"project_id": project.project_id, "phase": project.phase.value, "status": "cancelled"}
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/provide-data")
def research_provide_data(project_id: str, request: ProvideDataRequest) -> dict:
    try:
        project = research_orchestrator.provide_data(
            project_id,
            request.artifact_paths,
            request.description,
            request.data_type,
        )
        return {"project_id": project.project_id, "phase": project.phase.value, "status": "data_registered"}
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/run-dataset-tools")
def research_run_dataset_tools(project_id: str) -> dict:
    try:
        project = research_orchestrator.run_registered_dataset_tools(project_id)
        return {
            "project_id": project.project_id,
            "phase": project.phase.value,
            "status": "dataset_tools_completed",
            "execution_summary": project.internal_execution_summary,
        }
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


class ComputationApproval(BaseModel):
    plan_id: str


class ComputationFeedback(ComputationApproval):
    feedback: str


@app.post('/api/research/{project_id}/computation/feedback')
def computation_feedback(project_id: str, request: ComputationFeedback):
    if not request.feedback.strip() or len(request.feedback) > 8000:
        raise HTTPException(422, 'Provide feedback of 1–8000 characters')
    with computation_queue_lock:
        if research_job_store.active_step_job(project_id):
            raise HTTPException(409, 'Research is running; pause before revising the plan')
        project = research_orchestrator.get_project(project_id)
        state = project.computation
        if state.get('plan_id') != request.plan_id or state.get('status') != 'awaiting_approval':
            raise HTTPException(409, 'This plan is no longer awaiting approval')
        state.setdefault('human_feedback', []).append({'plan_id': request.plan_id, 'feedback': request.feedback.strip()})
        state['status'] = 'needs_input'
        research_orchestrator.store.save(project)
    return {'status': 'feedback_saved', 'project_id': project_id}


def _run_computation_job(project_id: str, job_id: str, operation: str, plan_id: str | None):
    from src.ai_scientist.schemas import utc_now
    job = research_job_store.load(project_id, job_id)
    job.status, job.started_at = 'running', utc_now()
    research_job_store.save(job)
    try:
        with model_progress(lambda event: research_job_store.record_model_progress(project_id, job_id, event)):
            if operation == 'prepare':
                project = research_orchestrator.get_project(project_id)
                result = computation_service.prepare(project_id, revision_request=project.computation.get('pending_revision_request') or project.computation.get('revision_request'))
            elif operation == 'resume_assessment':
                result = computation_service.resume_assessment(project_id)
            else:
                result = computation_service.execute(project_id, plan_id)
        job = research_job_store.load(project_id, job_id)
        job.result = {'status': result['status'], 'project_id': project_id}
        job.status, job.finished_at = 'completed', utc_now()
    except ModelInputRequired:
        job = research_job_store.load(project_id, job_id)
        job.status, job.finished_at = 'completed', utc_now()
        job.result = {'status': 'awaiting_model_requested_input', 'project_id': project_id}
    except Exception as exc:
        job = research_job_store.load(project_id, job_id)
        job = fail_job(job, exc)
    research_job_store.save(job)


def _queue_computation(project_id: str, operation: str, plan_id: str | None = None):
    try:
        project = research_orchestrator.get_project(project_id)
        computation_service.check(project, revision_request=project.computation.get('pending_revision_request'))
        with computation_queue_lock:
            if research_job_store.active_step_job(project_id):
                raise HTTPException(409, 'Project already has an active job')
            job = research_job_store.create(project_id, project.phase.value)
            threading.Thread(target=_run_computation_job,
                             args=(project_id, job.job_id, operation, plan_id), daemon=True).start()
        return {'job_id': job.job_id, 'project_id': project_id, 'status': 'queued'}
    except HTTPException:
        raise
    except Exception as exc:
        raise _research_http_error(exc) from exc


@app.post('/api/research/{project_id}/computation/prepare', status_code=202)
def research_prepare_computation(project_id: str):
    return _queue_computation(project_id, 'prepare')


@app.post('/api/research/{project_id}/computation/resume_assessment', status_code=202)
def research_resume_assessment(project_id: str):
    return _queue_computation(project_id, 'resume_assessment')


@app.get('/api/research/{project_id}/computation/file')
def research_computation_file(project_id: str, path: str):
    project = research_orchestrator.get_project(project_id)
    state = project.computation
    attempts = list(state.get('attempts', []))
    for record in state.get('rounds', []):
        attempts.extend(record['attempts'])
    attempts.extend(r['audit'] for r in project.research_workspace.get('runs', []))
    allowed = {a.get('code_path') for a in attempts}
    allowed.update(f['relative_path'] for a in attempts for f in a.get('artifacts', []))
    if path not in allowed:
        raise HTTPException(404, 'Artifact not in computation manifest')
    root = research_orchestrator.store.project_dir(project_id).resolve()
    target = root / path
    if target.is_symlink() or not target.resolve().is_relative_to(root) or not target.is_file():
        raise HTTPException(404, 'Artifact unavailable')
    if target.stat().st_size > 16 * 1024**2:
        raise HTTPException(413, 'Artifact too large')
    return {'filename': target.name, 'content_base64': base64.b64encode(target.read_bytes()).decode()}


@app.post('/api/research/{project_id}/computation/execute', status_code=202)
def research_execute_computation(project_id: str, request: ComputationApproval):
    return _queue_computation(project_id, 'execute', request.plan_id)


class WorkspaceRunRequest(BaseModel):
    max_steps: int = Field(default=6, ge=1, le=12)
    allow_network: bool = False
    feedback: str = Field(default='', max_length=4000)


class WorkspaceRejectRequest(ComputationApproval):
    reason: str = Field(default='', max_length=4000)


def _run_workspace_job(project_id, job_id, operation, arguments):
    from src.ai_scientist.schemas import utc_now
    job = research_job_store.load(project_id, job_id)
    job.status, job.started_at = 'running', utc_now()
    research_job_store.save(job)
    try:
        result = getattr(workspace_service, operation)(project_id, **arguments)
        job.result = {'status': result['status'], 'project_id': project_id, 'workflow': 'adaptive_workspace'}
        job.status, job.finished_at = 'completed', utc_now()
    except Exception as exc:
        job = fail_job(job, exc)
    research_job_store.save(job)


def _queue_workspace(project_id, operation, arguments):
    try:
        research_orchestrator.get_project(project_id)
        with computation_queue_lock:
            if research_job_store.active_step_job(project_id):
                raise HTTPException(409, 'Project already has an active job')
            job = research_job_store.create(project_id, 'ADAPTIVE_RESEARCH')
            threading.Thread(target=_run_workspace_job,
                             args=(project_id, job.job_id, operation, arguments), daemon=True).start()
        return {'job_id': job.job_id, 'project_id': project_id, 'status': 'queued'}
    except HTTPException:
        raise
    except Exception as exc:
        raise _research_http_error(exc) from exc


@app.post('/api/research/{project_id}/workspace/run', status_code=202)
def research_workspace_run(project_id: str, request: WorkspaceRunRequest):
    raise HTTPException(410, '自主工作台已归档。请通过项目的固定科研阶段继续；历史记录和报告仍可读取。')


@app.post('/api/research/{project_id}/workspace/execute', status_code=202)
def research_workspace_execute(project_id: str, request: ComputationApproval):
    raise HTTPException(410, '自主工作台已归档。计算须在固定工作流的方案审批后重新生成并批准。')


@app.post('/api/research/{project_id}/workspace/reject')
def research_workspace_reject(project_id: str, request: WorkspaceRejectRequest):
    raise HTTPException(410, '自主工作台已归档，历史记录为只读。')


@app.post('/api/research/{project_id}/workspace/pause')
def research_workspace_pause(project_id: str):
    raise HTTPException(410, '自主工作台已归档，历史记录为只读。')


@app.get('/api/research/{project_id}/workspace/report.md', response_class=PlainTextResponse)
def research_workspace_report(project_id: str):
    project = research_orchestrator.get_project(project_id)
    report = workspace_markdown(project)
    if project.research_workspace.get('report'):
        _, _, fingerprint = workspace_service._context(project)
        if fingerprint != project.research_workspace.get('report_context_fingerprint'):
            report = '# 注意：研究输入已改变，此报告属于旧版本，需重新研究与复审\n\n' + report
    return report


@app.post("/api/research/{project_id}/controlled-python")
def research_run_controlled_python(project_id: str, request: ControlledPythonRunRequest) -> dict:
    if not _controlled_python_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Controlled Python is disabled. Set AI_SCIENTIST_ENABLE_CONTROLLED_PYTHON=1 to enable the experimental runner.",
        )
    try:
        project, audit = research_orchestrator.run_controlled_python(
            project_id,
            code=request.code,
            asset_id=request.asset_id,
            timeout_seconds=request.timeout_seconds,
            memory_limit_mb=request.memory_limit_mb,
            seed=request.seed,
        )
        return {
            "project_id": project.project_id,
            "phase": project.phase.value,
            "status": audit["status"],
            "audit": audit,
        }
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.patch("/api/research/{project_id}/question")
def research_patch_question(project_id: str, request: HumanEditRequest) -> dict:
    try:
        return research_orchestrator.patch_question(project_id, request.patch, request.reason).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.patch("/api/research/{project_id}/hypotheses/{hypothesis_id}")
def research_patch_hypothesis(project_id: str, hypothesis_id: str, request: HumanEditRequest) -> dict:
    try:
        return research_orchestrator.patch_hypothesis(
            project_id,
            hypothesis_id,
            request.patch,
            request.reason,
        ).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.patch("/api/research/{project_id}/study-design")
def research_patch_study_design(project_id: str, request: HumanEditRequest) -> dict:
    try:
        return research_orchestrator.patch_study_design(project_id, request.patch, request.reason).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.patch("/api/research/{project_id}/analysis-plan")
def research_patch_analysis_plan(project_id: str, request: HumanEditRequest) -> dict:
    try:
        return research_orchestrator.patch_analysis_plan(project_id, request.patch, request.reason).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/evidence")
def research_add_evidence(project_id: str, request: EvidenceCreateRequest) -> dict:
    try:
        return research_orchestrator.add_evidence(project_id, request.evidence, request.reason).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.delete("/api/research/{project_id}/evidence/{evidence_id}")
def research_delete_evidence(project_id: str, evidence_id: str, reason: str = "") -> dict:
    try:
        return research_orchestrator.delete_evidence(project_id, evidence_id, reason).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/cancel")
def research_cancel(project_id: str) -> dict:
    try:
        project = research_orchestrator.cancel_project(project_id)
        return {"project_id": project.project_id, "phase": project.phase.value, "status": "cancelled"}
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.get("/api/research/{project_id}/claims")
def research_claims(project_id: str) -> list[dict]:
    try:
        return [item.model_dump(mode="json") for item in research_orchestrator.get_project(project_id).claims]
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.get("/api/research/{project_id}/evidence")
def research_evidence(project_id: str) -> list[dict]:
    try:
        return [item.model_dump(mode="json") for item in research_orchestrator.get_project(project_id).evidence]
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.get("/api/research/{project_id}/hypotheses")
def research_hypotheses(project_id: str) -> list[dict]:
    try:
        return [item.model_dump(mode="json") for item in research_orchestrator.get_project(project_id).hypotheses]
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.get("/api/research/{project_id}/artifacts")
def research_artifacts(project_id: str) -> list[dict]:
    try:
        return research_orchestrator.list_artifacts(project_id)
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.get("/api/research/{project_id}/report.md", response_class=PlainTextResponse)
def research_report_markdown(project_id: str) -> str:
    try:
        project = research_orchestrator.get_project(project_id)
        report = project.computation.get('delivery_report') or {}
        filename = report.get('filename') or 'research_plan.md'
        if Path(filename).name != filename:
            raise ValueError('Invalid report filename')
        path = research_orchestrator.store.project_dir(project.project_id) / "artifacts" / filename
        if not path.exists():
            raise FileNotFoundError("research_plan.md has not been generated yet.")
        return path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.get("/api/research/{project_id}/report.json")
def research_report_json(project_id: str) -> dict:
    try:
        import json

        project = research_orchestrator.get_project(project_id)
        path = research_orchestrator.store.project_dir(project.project_id) / "artifacts" / "research_plan.json"
        if not path.exists():
            raise FileNotFoundError("research_plan.json has not been generated yet.")
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.get("/api/research/{project_id}/events")
def research_events(project_id: str) -> list[dict]:
    try:
        return [item.model_dump(mode="json") for item in research_orchestrator.list_events(project_id)]
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.get("/api/research/{project_id}/capabilities")
def research_capabilities(project_id: str) -> dict:
    try:
        return research_orchestrator.capabilities(project_id)
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


def _research_http_error(exc: Exception) -> HTTPException:
    error_type = type(exc).__name__
    status_code = (
        404
        if error_type in {"ProjectNotFoundError", "ResearchAssetNotFoundError", "FileNotFoundError"}
        else 400
        if error_type == "ValueError"
        else 409
        if error_type == "InvalidTransitionError"
        else 500
    )
    return HTTPException(
        status_code=status_code,
        detail={
            "mode": "ai_scientist",
            "error_type": error_type,
            "error_message": _sanitize(str(exc)),
            "stage": getattr(exc, "stage", None),
            "stage_substep": getattr(exc, "substep", None),
            "failing_component": getattr(exc, "failing_component", None),
            "failure_category": getattr(exc, "failure_category", None),
            "artifact_type": getattr(exc, "artifact_type", None),
            "cause_type": getattr(exc, "cause_type", None),
            "cause_message": _sanitize(str(getattr(exc, "cause_message", "") or "")),
            "validation_errors": getattr(exc, "validation_errors", []),
        },
    )
