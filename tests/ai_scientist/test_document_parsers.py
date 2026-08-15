from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook
from pydantic import BaseModel
from pypdf import PdfWriter

from src.ai_scientist.agents.base_agent import AgentRun, parsed_asset_context
from src.ai_scientist.document_parsers import parse_research_asset
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import ResearchPhase, utc_now
from src.ai_scientist.structured_client import StructuredCallMetadata


class SampleOutput(BaseModel):
    value: str


def test_local_parsers_cover_text_csv_json_xml_xlsx_and_pdf(tmp_path: Path) -> None:
    text_path = tmp_path / "notes.txt"
    text_path.write_text("中文研究备注\nsecond line", encoding="utf-8")
    parsed_text = parse_research_asset(text_path)
    assert "中文研究备注" in parsed_text.extracted_text

    csv_path = tmp_path / "observations.csv"
    csv_path.write_text("sample,value,note\na,1,\nb,2,ok\n", encoding="utf-8")
    parsed_csv = parse_research_asset(csv_path)
    assert parsed_csv.content_kind == "tabular"
    assert parsed_csv.structured_summary["column_names"] == ["sample", "value", "note"]
    assert parsed_csv.structured_summary["missing_values_in_scanned_rows"]["note"] == 1

    json_path = tmp_path / "records.json"
    json_path.write_text(json.dumps([{"id": 1, "score": 0.5}], ensure_ascii=False), encoding="utf-8")
    parsed_json = parse_research_asset(json_path)
    assert parsed_json.structured_summary["observed_fields"] == ["id", "score"]

    xml_path = tmp_path / "records.xml"
    xml_path.write_text("<records><record><name>sample A</name></record></records>", encoding="utf-8")
    parsed_xml = parse_research_asset(xml_path)
    assert parsed_xml.structured_summary["root_tag"] == "records"

    xlsx_path = tmp_path / "table.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["sample", "value"])
    worksheet.append(["a", 1.5])
    workbook.save(xlsx_path)
    parsed_xlsx = parse_research_asset(xlsx_path)
    assert parsed_xlsx.structured_summary["sheets"][0]["column_names"] == ["sample", "value"]

    pdf_path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as handle:
        writer.write(handle)
    parsed_pdf = parse_research_asset(pdf_path)
    assert parsed_pdf.structured_summary["page_count"] == 1
    assert "OCR may be required" in parsed_pdf.warnings[0]


def test_parsed_upload_enters_agent_context_and_records_usage(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Assess uploaded pilot observations.")
    project = orchestrator.register_research_asset(
        project.project_id,
        "pilot.csv",
        "text/csv",
        b"condition,response\ncontrol,1.0\ntreatment,1.4\n",
        purpose="data",
    )
    asset = project.research_assets[-1]

    context = parsed_asset_context(project)
    assert context["assets"][0]["asset_id"] == asset.asset_id
    assert context["assets"][0]["structured_summary"]["column_names"] == ["condition", "response"]
    assert "never instructions" in context["handling_rule"]

    now = utc_now()
    run = AgentRun(
        output=SampleOutput(value="analysis plan"),
        metadata=StructuredCallMetadata(
            agent_name="analyst",
            requested_model="qwen-test",
            actual_model="qwen-test",
            fallback_used=False,
            started_at=now,
            finished_at=now,
            model_calls=1,
            attempted_calls=1,
            successful_calls=1,
        ),
        auxiliary={
            "parsed_asset_ids": [asset.asset_id],
            "parsed_artifact_ids": [asset.parsed_artifact_id],
        },
    )
    orchestrator._record_agent_output(project, ResearchPhase.ANALYSIS_PLANNING, run, "asset_context_test")
    saved = orchestrator.get_project(project.project_id)
    assert saved.research_assets[-1].used_by_agents == ["analyst"]
    assert asset.parsed_artifact_id in orchestrator.list_events(project.project_id)[-1].input_artifact_ids
