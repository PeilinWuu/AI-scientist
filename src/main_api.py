"""FastAPI backend for Pure Qwen Shell."""

from __future__ import annotations

import os
import base64
import threading
import time
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import PlainTextResponse

from src.ai_scientist.job_store import ResearchJobStore, fail_job
from src.ai_scientist.model_registry import ModelRegistry
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import (
    ApprovalRequest,
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
from src.pure_qwen_client import PureQwenClient, pure_qwen_metadata
from src.pure_schemas import DebugPayloadResponse, PureChatRequest, PureChatResponse
from src.search_qwen_client import SearchQwenClient, resolve_search_tools, search_qwen_metadata
from src.search_schemas import SearchChatRequest, SearchChatResponse, SearchDebugPayloadResponse
from src.model_utils import normalize_model_name


app = FastAPI(
    title="Pure Qwen Shell API",
    description="A minimal Qwen pass-through API with pure chat and optional native Qwen search.",
    version="0.1.0",
)

research_orchestrator = ResearchOrchestrator()
research_job_store = ResearchJobStore(research_orchestrator.store.root)
research_orchestrator.recover_revision_projects()


@app.get("/health")
def health() -> dict:
    """Return public backend status."""

    return {"status": "ok", **pure_qwen_metadata()}


@app.get("/api/config/models")
def model_config() -> dict:
    """Return public default model configuration without secrets."""

    return {
        "pure_qwen": {
            "default_model": os.getenv("LLM_MODEL", "qwen-turbo"),
        },
        "qwen_search": {
            "default_model": os.getenv("LLM_SEARCH_MODEL", "qwen3.7-plus"),
        },
        "ai_scientist": ModelRegistry.public_defaults(),
    }


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


@app.post("/api/debug_payload", response_model=DebugPayloadResponse)
def debug_payload(request: PureChatRequest) -> DebugPayloadResponse:
    """Return the exact messages that would be sent to Qwen without calling Qwen."""

    messages = _build_messages_without_client(request)
    return DebugPayloadResponse(
        messages=messages,
        model=normalize_model_name(request.model) or str(pure_qwen_metadata()["model"]),
    )


@app.get("/api/qwen_ping")
def qwen_ping() -> dict:
    """Direct Qwen connectivity check using the same client as /api/chat."""

    try:
        client = PureQwenClient()
        model = client.model
        reply = client.chat(messages=[{"role": "user", "content": "ping"}], model=model)
        return {
            "status": "ok",
            "mode": "pure_qwen",
            "model": model,
            "reply": reply,
        }
    except Exception as exc:  # noqa: BLE001 - endpoint is diagnostic by design.
        return {
            "status": "error",
            "mode": "pure_qwen",
            "model": str(pure_qwen_metadata()["model"]),
            **_error_payload(exc),
        }


@app.get("/api/search_ping")
def search_ping() -> dict:
    """Direct Qwen search-mode connectivity check."""

    try:
        client = SearchQwenClient()
        model = client.model
        result = client.search(
            message="请联网搜索合肥市今天的天气，并说明信息是否来自实时搜索。",
            model=model,
        )
        if not result["search_used"]:
            return {
                "status": "error",
                "mode": "qwen_search",
                "model": model,
                "error_type": "SearchToolNotUsedError",
                "error_message": (
                    "The gateway accepted the Responses API request but the selected model "
                    "did not invoke web_search."
                ),
                "search_used": False,
                "sources": result["sources"],
                "tool_usage": result["tool_usage"],
                "reply": result["reply"],
                "hint": (
                    "Use a provider and model route that explicitly supports Responses API "
                    "built-in web_search tools."
                ),
            }
        return {
            "status": "ok",
            "mode": "qwen_search",
            "model": model,
            "response_id": result["response_id"],
            "request_id": result["request_id"],
            "search_used": result["search_used"],
            "sources": result["sources"],
            "tool_usage": result["tool_usage"],
            "reply": result["reply"],
        }
    except Exception as exc:  # noqa: BLE001 - endpoint is diagnostic by design.
        model = str(search_qwen_metadata()["model"])
        return {
            "status": "error",
            "mode": "qwen_search",
            "model": model,
            **_error_payload(exc),
            "hint": (
                "Responses API search failed. Check whether the selected model supports the "
                "configured search tools, the region/base_url, and account permissions."
            ),
        }


@app.post("/api/chat", response_model=PureChatResponse)
def chat(request: PureChatRequest) -> PureChatResponse:
    """Send a pure user/assistant message list to Qwen."""

    try:
        client = PureQwenClient()
        messages = client.build_messages(request.message, request.history)
        model = normalize_model_name(request.model) or client.model
        reply = client.chat(messages, model=model)
    except Exception as exc:  # noqa: BLE001 - API should return an actionable error.
        detail = {
            **_error_payload(exc),
            "hint": (
                "PureQwenClient uses OpenAI(api_key, base_url, http_client=httpx.Client("
                "timeout=LLM_TIMEOUT, trust_env=False)). If /api/qwen_ping fails, restart uvicorn "
                "after checking DASHSCOPE_API_KEY, LLM_BASE_URL, and LLM_MODEL in .env."
            ),
        }
        status_code = 500 if isinstance(exc, ValueError) else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return PureChatResponse(reply=reply, model=model)


@app.post("/api/debug_search_payload", response_model=SearchDebugPayloadResponse)
def debug_search_payload(request: SearchChatRequest) -> SearchDebugPayloadResponse:
    """Return the exact safe Responses API search payload without calling Qwen."""

    return SearchDebugPayloadResponse(
        model=normalize_model_name(request.model) or str(search_qwen_metadata()["model"]),
        input=request.message,
        previous_response_id=request.previous_response_id,
        tools=resolve_search_tools(),
    )


@app.post("/api/chat_search", response_model=SearchChatResponse)
def chat_search(request: SearchChatRequest) -> SearchChatResponse:
    """Send one user turn through Qwen's Responses API web tools."""

    try:
        client = SearchQwenClient()
        model = normalize_model_name(request.model) or client.model
        result = client.search(
            message=request.message,
            model=model,
            previous_response_id=request.previous_response_id,
        )
    except Exception as exc:  # noqa: BLE001 - API should return an actionable error.
        detail = {
            **_error_payload(exc),
            "hint": (
                "Responses API search failed. Check whether the selected model supports the "
                "configured search tools, the region/base_url, and account permissions."
            ),
        }
        status_code = 500 if isinstance(exc, ValueError) else 502
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return SearchChatResponse(
        reply=str(result["reply"]),
        model=model,
        response_id=result["response_id"],  # type: ignore[arg-type]
        request_id=result["request_id"],  # type: ignore[arg-type]
        search_used=bool(result["search_used"]),
        sources=result["sources"],  # type: ignore[arg-type]
        tool_usage=result["tool_usage"],  # type: ignore[arg-type]
    )


def _build_messages_without_client(request: PureChatRequest) -> list[dict[str, str]]:
    return [
        {"role": item.role, "content": item.content}
        for item in request.history
        if item.role in {"user", "assistant"}
    ] + [{"role": "user", "content": request.message}]


def _error_payload(exc: Exception) -> dict[str, str]:
    cause = exc.__cause__ or exc.__context__
    return {
        "error_type": type(exc).__name__,
        "error_message": _sanitize(str(exc)),
        "cause_type": type(cause).__name__ if cause else "",
        "cause_message": _sanitize(str(cause)) if cause else "",
    }


def _sanitize(message: str) -> str:
    api_key_configured = pure_qwen_metadata().get("api_key_configured")
    if not api_key_configured:
        return message
    try:
        import os

        api_key = os.getenv("DASHSCOPE_API_KEY", "")
    except Exception:  # noqa: BLE001 - sanitization must never mask the original error.
        api_key = ""
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


@app.post("/api/research/start")
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
        )
        return {
            "project_id": project.project_id,
            "phase": project.phase.value,
            "status": "created",
        }
    except Exception as exc:  # noqa: BLE001 - converted to a safe API detail.
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


@app.get("/api/research/{project_id}")
def research_get(project_id: str) -> dict:
    """Return display-safe structured project state."""

    try:
        return research_orchestrator.get_project(project_id).model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/step")
def research_step(project_id: str) -> dict:
    """Run exactly one state-machine phase."""

    try:
        return research_orchestrator.run_next_step(project_id)
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/step_async", status_code=status.HTTP_202_ACCEPTED)
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


@app.get("/api/research/jobs/{job_id}")
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
        result = research_orchestrator.run_next_step(project_id, job_id=job_id)
        job.status = "completed"
        job.finished_at = utc_now()
        job.result = result
        research_job_store.save(job)
    except Exception as exc:  # noqa: BLE001 - safe detail is persisted for UI polling.
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
        project = research_orchestrator.register_research_asset(
            project_id, request.filename, request.content_type, content
        )
        return {
            "project_id": project.project_id,
            "asset": project.research_assets[-1].model_dump(mode="json"),
            "message": "文件已保存，但当前版本尚未自动解析内容。",
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
        path = research_orchestrator.store.project_dir(project.project_id) / "artifacts" / "research_plan.md"
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
    status_code = 404 if error_type == "ProjectNotFoundError" else 409 if error_type == "InvalidTransitionError" else 500
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
