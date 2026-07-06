"""FastAPI backend for FlowScientist-Loop."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from src.llm import get_llm_status
from src.schemas import HumanFeedbackRequest, RunRequest, RunResponse
from src.workflow.experiment_loop import (
    add_human_feedback,
    get_report,
    get_run,
    run_experiment_loop,
)


app = FastAPI(
    title="FlowScientist-Loop API",
    description="Multi-agent virtual experiment planning and feedback iteration prototype.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict:
    """Basic service health check."""

    return {"status": "ok", **get_llm_status()}


@app.post("/api/run", response_model=RunResponse)
def run_loop(request: RunRequest) -> RunResponse:
    """Start one full experiment loop."""

    summary = run_experiment_loop(request)
    return RunResponse(run_id=summary.run_id, summary=summary)


@app.get("/api/runs/{run_id}")
def read_run(run_id: str) -> dict:
    """Return all iteration logs for a run."""

    try:
        return get_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/report", response_class=PlainTextResponse)
def read_report(run_id: str) -> str:
    """Return the final Markdown report."""

    try:
        return get_report(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/human_feedback")
def append_feedback(run_id: str, request: HumanFeedbackRequest) -> dict:
    """Append human feedback and update the run report."""

    try:
        return add_human_feedback(run_id, request.human_feedback)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
