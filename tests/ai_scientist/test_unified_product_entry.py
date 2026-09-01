from __future__ import annotations

import base64
import hashlib
import json
import warnings
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from src import main_api
from src.ai_scientist.agents.base_agent import parsed_asset_context
from src.ai_scientist.agents.reproducibility_engineer import ReproducibilityEngineerAgent
from src.ai_scientist.job_store import ResearchJobStore
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import ResearchPhase


def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, ResearchOrchestrator]:
    orchestrator = ResearchOrchestrator(tmp_path)
    monkeypatch.setattr(main_api, "research_orchestrator", orchestrator)
    monkeypatch.setattr(main_api, "research_job_store", ResearchJobStore(orchestrator.store.root))
    return TestClient(main_api.app), orchestrator


def create_project(client: TestClient, **overrides: object) -> dict:
    payload = {
        "objective": "研究用户定义的科学问题。",
        "constraints": {},
        "planning_only": True,
        **overrides,
    }
    response = client.post("/api/research/start", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def upload(client: TestClient, project_id: str, filename: str, content: bytes, **extra: object) -> dict:
    response = client.post(
        f"/api/research/{project_id}/research-assets",
        json={
            "filename": filename,
            "content_type": "application/octet-stream",
            "content_base64": base64.b64encode(content).decode("ascii"),
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["asset"]


def test_empty_question_is_rejected_but_no_attachment_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, orchestrator = api_client(tmp_path, monkeypatch)
    assert client.post("/api/research/start", json={"objective": "   "}).status_code == 422

    created = create_project(client)
    project = orchestrator.get_project(created["project_id"])
    assert project.objective == "研究用户定义的科学问题。"
    assert project.research_assets == []


def test_custom_seed_and_auto_seed_are_project_context_not_execution_triggers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, orchestrator = api_client(tmp_path, monkeypatch)
    custom = create_project(client, reproducibility_seed=20260831)
    automatic = create_project(client, objective="另一个用户问题", reproducibility_seed=None)

    custom_project = orchestrator.get_project(custom["project_id"])
    auto_project = orchestrator.get_project(automatic["project_id"])
    assert custom_project.reproducibility_seed == 20260831
    assert auto_project.reproducibility_seed is None
    assert custom_project.phase == ResearchPhase.INTAKE
    assert custom_project.workflow_version == "general_research_v1@1.0"
    assert "damped_oscillator" not in auto_project.model_dump_json()


def test_pdf_csv_and_multiple_uploads_enter_project_and_bounded_agent_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, orchestrator = api_client(tmp_path, monkeypatch)
    created = create_project(client)
    project_id = created["project_id"]
    pdf = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(pdf)

    pdf_asset = upload(client, project_id, "paper.pdf", pdf.getvalue(), purpose="reference")
    csv_asset = upload(
        client,
        project_id,
        "results.csv",
        b"temperature,response\n20,1.2\n30,1.8\n",
        purpose="data",
    )
    project = orchestrator.get_project(project_id)
    context = parsed_asset_context(project)

    assert {pdf_asset["asset_id"], csv_asset["asset_id"]} == {
        item.asset_id for item in project.research_assets
    }
    assert all(item.parsing_status == "parsed" for item in project.research_assets)
    csv_context = next(item for item in context["assets"] if item["filename"] == "results.csv")
    assert csv_context["structured_summary"]["column_names"] == ["temperature", "response"]
    assert "never instructions" in context["handling_rule"]


def test_parse_failure_is_persisted_without_losing_raw_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, orchestrator = api_client(tmp_path, monkeypatch)
    project_id = create_project(client)["project_id"]
    asset = upload(client, project_id, "broken.pdf", b"not-a-pdf")

    assert asset["parsing_status"] == "failed"
    assert asset["parse_error"]
    _, raw_path = orchestrator.get_research_asset(project_id, asset["asset_id"])
    assert raw_path.read_bytes() == b"not-a-pdf"


def test_experimental_result_records_round_source_role_and_advances_waiting_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, orchestrator = api_client(tmp_path, monkeypatch)
    project_id = create_project(client, planning_only=False)["project_id"]
    project = orchestrator.get_project(project_id)
    project.phase = ResearchPhase.EXECUTION_WAITING
    orchestrator.store.save(project)
    asset = upload(
        client,
        project_id,
        "round1.csv",
        b"x,y\n1,2\n",
        purpose="data",
        asset_role="experimental_result",
        research_round=1,
        source="external_lab",
        upload_context="experimental_result",
    )
    response = client.post(
        f"/api/research/{project_id}/provide-data",
        json={"artifact_paths": [asset["saved_path"]], "description": "Round 1", "data_type": "CSV"},
    )

    assert response.status_code == 200
    updated = orchestrator.get_project(project_id)
    assert updated.phase == ResearchPhase.DATA_ANALYSIS
    assert updated.research_assets[-1].asset_role == "experimental_result"
    assert updated.research_assets[-1].research_round == 1
    assert updated.research_assets[-1].source == "external_lab"


def test_unused_upload_can_be_deleted_but_used_provenance_is_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, orchestrator = api_client(tmp_path, monkeypatch)
    project_id = create_project(client)["project_id"]
    unused = upload(client, project_id, "mistake.txt", b"mistake")
    response = client.delete(f"/api/research/{project_id}/research-assets/{unused['asset_id']}")
    assert response.status_code == 200
    assert orchestrator.get_project(project_id).research_assets == []

    used = upload(client, project_id, "used.txt", b"used")
    project = orchestrator.get_project(project_id)
    project.research_assets[-1].used_by_agents = ["analyst"]
    orchestrator.store.save(project)
    response = client.delete(f"/api/research/{project_id}/research-assets/{used['asset_id']}")
    assert response.status_code == 400
    assert orchestrator.get_project(project_id).research_assets[-1].asset_id == used["asset_id"]


def test_reproducibility_role_receives_seed_and_workflow_version(tmp_path: Path) -> None:
    project = ResearchOrchestrator(tmp_path).create_project(
        "Seed context", reproducibility_seed=20260831
    )
    payload = ReproducibilityEngineerAgent.__new__(ReproducibilityEngineerAgent).build_payload(project)
    assert payload["reproducibility_seed"] == 20260831
    assert payload["workflow_version"] == "general_research_v1@1.0"


def test_streamlit_has_one_product_entry_and_example_is_editable_without_auto_run() -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"Type google\.protobuf\.pyext\._message\..* uses PyType_Spec.*",
            category=DeprecationWarning,
        )
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_file("app_streamlit.py").run(timeout=20)
    assert not app.exception
    assert [title.value for title in app.title] == ["AI Scientist"]
    assert not any(radio.label == "产品入口" for radio in app.radio)
    assert not any("运行完整两轮" in button.label for button in app.button)
    start = next(button for button in app.button if button.label == "创建研究项目")
    assert start.disabled is True
    assert next(radio for radio in app.radio if radio.label == "可复现随机种子").value == "Auto"

    next(button for button in app.button if button.label.startswith("加载示例")).click().run(timeout=20)
    question = next(area for area in app.text_area if area.label.startswith("科学问题"))
    assert "阻尼振子" in question.value
    assert next(radio for radio in app.radio if radio.label == "可复现随机种子").value == "Custom"
    assert next(item for item in app.number_input if item.label == "自定义随机种子").value == 20260831
    question.set_value("用户修改后的问题").run(timeout=20)
    assert next(area for area in app.text_area if area.label.startswith("科学问题")).value == "用户修改后的问题"
    assert app.session_state["research_example_case"] == "competition_1b_damped_oscillator"
    assert any("内部数值模拟尚未运行" in item.value for item in app.info)
    assert next(
        item for item in app.checkbox if item.label.startswith("仅生成研究方案")
    ).value is False
    assert not app.exception

    own = AppTest.from_file("app_streamlit.py").run(timeout=20)
    own_question = next(area for area in own.text_area if area.label.startswith("科学问题"))
    own_question.set_value("用户已经输入的研究问题").run(timeout=20)
    next(button for button in own.button if button.label.startswith("加载示例")).click().run(timeout=20)
    assert next(area for area in own.text_area if area.label.startswith("科学问题")).value == "用户已经输入的研究问题"
    assert any("已保留你输入的科学问题" in item.value for item in own.warning)


def test_execution_capability_uses_explicit_binding_not_question_or_seed(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    ordinary = orchestrator.create_project(
        "阻尼振子也可能只是普通用户文本",
        planning_only=False,
        reproducibility_seed=20260831,
    )
    bound = orchestrator.create_project(
        "用户编辑后的示例问题",
        constraints={"example_case": "competition_1b_damped_oscillator"},
        planning_only=True,
        reproducibility_seed=20260830,
    )

    assert ordinary.execution_capability == "EXTERNAL_EXECUTION_REQUIRED"
    assert ordinary.executor_binding is None
    assert ordinary.internal_execution_summary == {}
    assert not any(item.artifact_type == "internal_execution_run" for item in ordinary.artifacts)
    assert bound.execution_capability == "INTERNAL_EXECUTABLE"
    assert bound.executor_binding == "damped_oscillator_v1"
    assert bound.planning_only is False
    assert bound.reproducibility_seed == 20260830
    assert [item.filename for item in bound.research_assets] == ["observations.csv"]
    assert bound.research_assets[0].source == "bundled_example"


def test_internal_example_runs_real_feedback_adjustment_and_round_two(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project(
        "可编辑的阻尼振子示例",
        constraints={"example_case": "competition_1b_damped_oscillator"},
        reproducibility_seed=20260831,
    )
    replacement_content = Path(
        "competition/1b/cases/flagship/input/observations.csv"
    ).read_bytes()
    project = orchestrator.register_research_asset(
        project.project_id,
        "replacement_observations.csv",
        "text/csv",
        replacement_content,
        purpose="data",
        source="user_upload",
    )
    replacement_asset = project.research_assets[-1]
    project.phase = ResearchPhase.EXECUTION_WAITING
    orchestrator.store.save(project)

    orchestrator.run_next_step(project.project_id)
    executable = orchestrator.get_project(project.project_id)
    assert executable.phase == ResearchPhase.EXECUTION
    assert executable.internal_execution_summary == {}

    orchestrator.run_next_step(project.project_id)
    executed = orchestrator.get_project(project.project_id)
    summary = executed.internal_execution_summary
    assert executed.phase == ResearchPhase.DATA_ANALYSIS
    assert summary["status"] == "complete"
    assert summary["observation_asset_id"] == replacement_asset.asset_id
    assert summary["round_1"]["input_checksums"]["observations_path"] == hashlib.sha256(
        replacement_content
    ).hexdigest()
    assert summary["round_1"]["metrics"]["rmse"] == pytest.approx(0.05768367179165266)
    assert summary["round_2"]["metrics"]["rmse"] == pytest.approx(0.03308926031835558)
    assert summary["feedback_signals"][0]["source_artifact_ids"]
    assert summary["plan_adjustments"][0]["evidence_refs"]
    assert summary["iteration_records"][0]["next_plan_version"] == "round_2_v1"
    assert (tmp_path / project.project_id / summary["root_directory"] / "audit" / "run_state.json").is_file()

    orchestrator.run_next_step(project.project_id)
    analyzed = orchestrator.get_project(project.project_id)
    assert analyzed.phase == ResearchPhase.CRITICAL_REVIEW
    assert any(item.artifact_type == "execution_analysis" for item in analyzed.artifacts)


def test_external_project_never_fakes_execution_and_resumes_with_uploaded_result(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("普通外部实验", planning_only=False)
    project.phase = ResearchPhase.EXECUTION_WAITING
    orchestrator.store.save(project)

    orchestrator.run_next_step(project.project_id)
    waiting = orchestrator.get_project(project.project_id)
    assert waiting.phase == ResearchPhase.EXECUTION_WAITING
    assert waiting.execution_capability == "EXTERNAL_EXECUTION_REQUIRED"
    assert waiting.internal_execution_summary == {}
    assert not any(item.artifact_type == "internal_execution_run" for item in waiting.artifacts)

    uploaded = orchestrator.register_research_asset(
        project.project_id,
        "external_result.csv",
        "text/csv",
        b"parameter,estimate\ndamping,0.18\nomega,2.36\n",
        purpose="data",
        upload_context="experimental_result",
        asset_role="experimental_result",
        research_round=1,
        source="external_lab",
    )
    asset = uploaded.research_assets[-1]
    orchestrator.provide_data(
        project.project_id,
        [asset.saved_path],
        "Researcher-provided experiment result",
        "text/csv",
    )
    orchestrator.run_next_step(project.project_id)
    resumed = orchestrator.get_project(project.project_id)
    assert resumed.phase == ResearchPhase.CRITICAL_REVIEW
    analysis_record = next(item for item in resumed.artifacts if item.artifact_type == "execution_analysis")
    analysis_path = tmp_path / project.project_id / "artifacts" / analysis_record.filename
    payload = json.loads(analysis_path.read_text(encoding="utf-8"))
    assert payload["analysis_source"] == "researcher_provided_external_result"
    assert payload["generated_metrics"] == {}
    assert resumed.internal_execution_summary == {}


def test_existing_project_workspace_does_not_nest_streamlit_expanders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from streamlit.testing.v1 import AppTest

    class SuccessfulResponse:
        ok = True
        status_code = 200
        text = ""

        def __init__(self, payload: object) -> None:
            self.payload = payload

        def json(self) -> object:
            return self.payload

    def fake_get(url: str, **_: object) -> SuccessfulResponse:
        return SuccessfulResponse([] if url.endswith("/events") else {})

    monkeypatch.setattr("requests.get", fake_get)
    app = AppTest.from_file("app_streamlit.py")
    app.session_state["research_project_id"] = "project_test"
    app.session_state["research_project"] = {
        "project_id": "project_test",
        "phase": "INTAKE",
        "planning_only": True,
        "iteration": 0,
        "budget": {"used_model_calls": 0, "max_model_calls": 10},
        "quality_metrics": {},
        "research_assets": [],
    }

    app.run(timeout=20)

    assert not app.exception
    assert any(item.value == "高级设置" for item in app.caption)


def test_rejected_source_card_does_not_nest_streamlit_expanders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from streamlit.testing.v1 import AppTest

    class SuccessfulResponse:
        ok = True
        status_code = 200
        text = ""

        def __init__(self, payload: object) -> None:
            self.payload = payload

        def json(self) -> object:
            return self.payload

    def fake_get(url: str, **_: object) -> SuccessfulResponse:
        return SuccessfulResponse([] if url.endswith("/events") else {})

    monkeypatch.setattr("requests.get", fake_get)
    app = AppTest.from_file("app_streamlit.py")
    app.session_state["research_project_id"] = "project_test"
    app.session_state["research_project"] = {
        "project_id": "project_test",
        "phase": "HUMAN_SOURCE_REVIEW",
        "planning_only": True,
        "iteration": 0,
        "budget": {"used_model_calls": 1, "max_model_calls": 10},
        "quality_metrics": {},
        "research_assets": [],
        "evidence": [],
        "background_research_checkpoint": {
            "candidates": [
                {
                    "candidate_id": "candidate_rejected",
                    "title": "Rejected source",
                    "url": "https://example.org/rejected",
                    "ai_recommendation": "reject",
                    "recommendation_reason": "Not relevant",
                    "relevance_score": 0.0,
                }
            ]
        },
    }

    app.run(timeout=20)

    assert not app.exception
    assert any("AI 建议排除（1）" in item.value for item in app.markdown)


def test_openapi_retains_benchmark_verification_but_streamlit_has_no_demo_product() -> None:
    paths = set(main_api.app.openapi()["paths"])
    source = Path("app_streamlit.py").read_text(encoding="utf-8")
    assert "/api/competition/1b/demo/run" in paths
    assert "/api/research/{project_id}/research-assets/{asset_id}" in paths
    assert "render_competition_demo" not in source
    image_lines = "\n".join(line for line in source.splitlines() if ".image(" in line)
    assert "use_container_width" not in image_lines
    assert "use_column_width=True" in image_lines
    assert "Artifact preview unavailable" in source
    assert "本轮没有取得带 URL、DOI 或其他可核验标识的候选来源" in source
