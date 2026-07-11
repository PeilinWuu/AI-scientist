"""FastAPI backend for Pure Qwen Shell."""

from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import PlainTextResponse

from src.ai_scientist.job_store import ResearchJobStore, fail_job
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import (
    EvidenceCreateRequest,
    HumanEditRequest,
    ProvideDataRequest,
    ResearchStartRequest,
    RevisionRequest,
)
from src.pure_qwen_client import PureQwenClient, pure_qwen_metadata
from src.pure_schemas import DebugPayloadResponse, PureChatRequest, PureChatResponse
from src.search_qwen_client import SEARCH_TOOLS, SearchQwenClient, search_qwen_metadata
from src.search_schemas import SearchChatRequest, SearchChatResponse, SearchDebugPayloadResponse


app = FastAPI(
    title="Pure Qwen Shell API",
    description="A minimal Qwen pass-through API with pure chat and optional native Qwen search.",
    version="0.1.0",
)

research_orchestrator = ResearchOrchestrator()
research_job_store = ResearchJobStore(research_orchestrator.store.root)


@app.get("/health")
def health() -> dict:
    """Return public backend status."""

    return {"status": "ok", **pure_qwen_metadata()}


@app.post("/api/debug_payload", response_model=DebugPayloadResponse)
def debug_payload(request: PureChatRequest) -> DebugPayloadResponse:
    """Return the exact messages that would be sent to Qwen without calling Qwen."""

    messages = _build_messages_without_client(request)
    return DebugPayloadResponse(
        messages=messages,
        model=request.model or str(pure_qwen_metadata()["model"]),
    )


@app.get("/api/qwen_ping")
def qwen_ping() -> dict:
    """Direct Qwen connectivity check using the same client as /api/chat."""

    model = "qwen-turbo"
    try:
        client = PureQwenClient()
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
                "web_search and web_extractor tools, the region/base_url, and account permissions."
            ),
        }


@app.post("/api/chat", response_model=PureChatResponse)
def chat(request: PureChatRequest) -> PureChatResponse:
    """Send a pure user/assistant message list to Qwen."""

    try:
        client = PureQwenClient()
        messages = client.build_messages(request.message, request.history)
        model = request.model or client.model
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
        model=request.model or str(search_qwen_metadata()["model"]),
        input=request.message,
        previous_response_id=request.previous_response_id,
        tools=[tool.copy() for tool in SEARCH_TOOLS],
    )


@app.post("/api/chat_search", response_model=SearchChatResponse)
def chat_search(request: SearchChatRequest) -> SearchChatResponse:
    """Send one user turn through Qwen's Responses API web tools."""

    try:
        client = SearchQwenClient()
        model = request.model or client.model
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
                "web_search and web_extractor tools, the region/base_url, and account permissions."
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


@app.post("/api/research/start")
def research_start(request: ResearchStartRequest) -> dict:
    """Create a persisted AI Scientist project without running a model stage."""

    try:
        project = research_orchestrator.create_project(
            objective=request.objective,
            domain_hint=request.domain_hint,
            constraints_text=request.constraints_text,
            constraints=request.constraints,
            max_iterations=request.max_iterations,
            planning_only=request.planning_only,
        )
        return {
            "project_id": project.project_id,
            "phase": project.phase.value,
            "status": "created",
        }
    except Exception as exc:  # noqa: BLE001 - converted to a safe API detail.
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
def research_approve(project_id: str) -> dict:
    try:
        project = research_orchestrator.approve_project(project_id)
        return {"project_id": project.project_id, "phase": project.phase.value, "status": "approved"}
    except Exception as exc:  # noqa: BLE001
        raise _research_http_error(exc) from exc


@app.post("/api/research/{project_id}/revise")
def research_revise(project_id: str, request: RevisionRequest) -> dict:
    try:
        project = research_orchestrator.request_revision(project_id, request.target, request.feedback)
        return {"project_id": project.project_id, "phase": project.phase.value, "status": "revision_requested"}
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
        },
    )
