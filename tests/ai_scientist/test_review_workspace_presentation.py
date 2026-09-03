from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.ai_scientist.exceptions import InvalidTransitionError
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.presentation import dedupe_user_events, render_event_dict
from src.ai_scientist.report_writer import build_research_plan_markdown
from src.ai_scientist.schemas import ResearchPhase, ReviewResult
from src.ui_time import format_local_datetime, format_utc_datetime


def _approval_project(orchestrator: ResearchOrchestrator):
    project = orchestrator.create_project("审查一个完整研究规划", planning_only=True)
    project.phase = ResearchPhase.HUMAN_APPROVAL
    project.reviews.append(
        ReviewResult(
            evidence_quality_score=8,
            methodological_validity_score=8,
            feasibility_score=8,
            reproducibility_score=8,
            claim_support_score=8,
            uncertainty_handling_score=8,
            decision="approve",
        )
    )
    project.active_artifact_versions.update(
        {
            "research_question": 1,
            "evidence_map": 1,
            "claim_evidence_mapping": 1,
            "hypotheses": 1,
            "study_design": 1,
            "analysis_plan": 1,
            "reproducibility_plan": 1,
            "independent_review": 1,
        }
    )
    orchestrator.store.save(project)
    return project


def test_approval_requires_acknowledgment_and_frozen_versions(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = _approval_project(orchestrator)
    package = orchestrator.get_review_package(project.project_id)

    with pytest.raises(InvalidTransitionError, match="acknowledgment"):
        orchestrator.approve_project(project.project_id)

    approved = orchestrator.approve_project(
        project.project_id, acknowledged=True, expected_versions=package.artifact_versions
    )
    assert approved.approval_status == "valid"
    assert approved.approval_valid_for_versions == package.artifact_versions
    assert approved.phase == ResearchPhase.SYNTHESIS


def test_post_execution_approval_goes_to_synthesis_without_rerunning(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = _approval_project(orchestrator)
    project.planning_only = False
    project.execution_capability = "INTERNAL_EXECUTABLE"
    project.executor_binding = "damped_oscillator_v1"
    project.internal_execution_summary = {"status": "complete", "run_id": "completed-run"}
    orchestrator.store.save(project)
    package = orchestrator.get_review_package(project.project_id)

    approved = orchestrator.approve_project(
        project.project_id, acknowledged=True, expected_versions=package.artifact_versions
    )

    assert approved.phase == ResearchPhase.SYNTHESIS
    assert approved.internal_execution_summary["run_id"] == "completed-run"
    assert len(approved.human_approval_history) == 1


def test_changed_artifact_version_invalidates_review_package(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = _approval_project(orchestrator)
    package = orchestrator.get_review_package(project.project_id)
    project = orchestrator.get_project(project.project_id)
    project.active_artifact_versions["study_design"] = 2
    orchestrator.store.save(project)

    with pytest.raises(InvalidTransitionError, match="stale"):
        orchestrator.approve_project(
            project.project_id, acknowledged=True, expected_versions=package.artifact_versions
        )


def test_defer_stays_in_human_approval_without_iteration(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = _approval_project(orchestrator)
    deferred = orchestrator.defer_approval(project.project_id, "还需要领域专家核对")
    assert deferred.phase == ResearchPhase.HUMAN_APPROVAL
    assert deferred.iteration == 0
    assert deferred.approval_status == "deferred"


def test_timeline_hides_internal_unknowns_and_dedupes() -> None:
    events = [
        {"phase": "BACKGROUND_RESEARCH", "visibility": "internal", "status": "completed", "display_key": "x"},
        {"phase": "BACKGROUND_RESEARCH", "visibility": "user", "status": "completed", "display_key": "done", "display_markdown": "证据检索完成。"},
        {"phase": "BACKGROUND_RESEARCH", "visibility": "user", "status": "completed", "display_key": "done", "display_markdown": "重复文本。"},
        {"phase": "STUDY_DESIGN", "visibility": "user", "status": "completed"},
    ]
    visible = dedupe_user_events(events)
    assert len(visible) == 1
    assert "证据检索完成" in render_event_dict(visible[0])
    assert render_event_dict(events[0]) == ""
    assert render_event_dict(events[3]) == ""


def test_ui_timezone_preserves_utc_and_displays_shanghai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UI_TIMEZONE", "Asia/Shanghai")
    value = datetime(2026, 8, 13, 2, 30, tzinfo=timezone.utc)
    assert "10:30:00" in format_local_datetime(value)
    assert format_utc_datetime(value) == "2026-08-13 02:30:00 UTC"


def test_report_has_natural_language_sections_without_json_fences(tmp_path: Path) -> None:
    project = ResearchOrchestrator(tmp_path).create_project("形成自然语言研究报告")
    report = build_research_plan_markdown(project)
    assert "## 11. Study Design" in report
    assert "## 13. Reproducibility Plan" in report
    assert "```json" not in report


def test_streamlit_has_one_keyed_research_download() -> None:
    source = Path("app_streamlit.py").read_text(encoding="utf-8")
    assert source.count('st.download_button(\n            "下载研究计划"') == 1
    assert 'key=f"research_plan_download_{project[\'project_id\']}"' in source
