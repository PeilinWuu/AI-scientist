"""Shared memory transport tests use recording doubles, never paid model calls."""

import json
from types import SimpleNamespace

import pytest

from src.ai_scientist.agents.analyst import AnalystAgent
from src.ai_scientist.agents.hypothesis_scientist import HypothesisScientistAgent
from src.ai_scientist.agents.methodologist import MethodologistAgent
from src.ai_scientist.agents.reproducibility_engineer import ReproducibilityEngineerAgent
from src.ai_scientist.agents.research_director import ResearchDirectorAgent
from src.ai_scientist.agents.scientific_synthesizer import ScientificSynthesizerAgent
from src.ai_scientist.agents.skeptical_reviewer import SkepticalReviewerAgent
from src.ai_scientist.agents.study_designer import StudyDesignerAgent
from src.ai_scientist.research_context import SHARED_CONTEXT_INSTRUCTION, build_research_context
from src.ai_scientist.schemas import EvidenceItem, HumanRevisionRecord, ResearchProject, RevisionIssue


@pytest.fixture
def project():
    p = ResearchProject(title="方法比较", objective="比较方法的覆盖率；模拟不是真实观测。",
                        constraints={"repetitions": 20000}, reproducibility_seed=1234)
    p.revision_feedback = ["旧反馈", "不要迎合旧假设，请检查逐次覆盖标志是否一致。"]
    p.pending_revision_target = "hypothesis"
    p.human_revision_history = [HumanRevisionRecord(
        project_id=p.project_id, target="hypothesis", feedback=p.revision_feedback[-1])]
    p.reproducibility_plan = {"archive": "交付实际运行的代码而不是占位脚本"}
    p.execution_requirements = ["报告成对标准误"]
    p.accepted_limitations = ["结果只适用于预先指定的参数范围"]
    p.revision_issues = [RevisionIssue(
        classification="plan_blocking", target="analysis_plan", problem="缺少独立验证",
        completion_criteria=["比较解析解与模拟误差"])]
    p.background_agent = {
        "status": "complete", "scope": {"external_information": "required"},
        "basis_approval": {"validation_is_planned_not_executed": True},
        "feedback": ["不要把反爬页面当作原文"], "adopted_source_ids": ["source-1"],
        "sources": [{"candidate_id": "source-1", "url": "https://example.org/paper",
                     "extracted_text": "SHOULD_NOT_SEND_WHOLE_PAPER" * 10000,
                     "retrieval_snapshot": {"url": "https://example.org/paper", "sha256": "hash-1", "truncated": True},
                     "extraction_status": "completed"}],
        "observations": [
            {"id": "obs-1", "action": "read_source", "status": "completed",
             "result": {"source_id": "source-1", "offset": 0, "text": "abcde", "total_chars": 100,
                        "download_truncated": True}},
            {"id": "obs-2", "action": "read_source", "status": "failed",
             "result": {"source_id": "source-1", "offset": 5, "text": "not shown"}},
            {"id": "obs-3", "action": "read_source", "status": "completed",
             "result": {"source_id": "source-1", "offset": 100, "text": "", "total_chars": 100,
                        "download_truncated": True}},
        ],
    }
    p.evidence = [EvidenceItem(evidence_id="EVD-001", title="A method paper", summary="Read only an excerpt",
                               source_url="https://example.org/paper", source_quote="abcde")]
    p.computation = {"status": "assessed", "rounds": [{"round": 1, "plan_id": "plan-1",
        "plan": {"method": "simulation", "code": "SHOULD_NOT_SEND_CODE"},
        "attempts": [{"run_id": "run-1", "status": "completed", "code_path": "computation/run-1/program.py",
                      "seed": 1234, "stdout": "SHOULD_NOT_SEND_LOGS"}],
        "assessment": {"decision": "iterate", "interpretation": "需要补充交付"}}]}
    return p


def test_shared_memory_retains_feedback_basis_and_actual_read_ranges(project):
    before = project.model_dump_json()
    context = build_research_context(project)
    assert context["research_task"]["objective"] == project.objective
    assert context["human_feedback"]["revision_feedback"][0] == project.revision_feedback[-1]
    assert context["human_feedback"]["recent_requests"][0]["target"] == "hypothesis"
    assert context["research_basis"]["status"] == "complete"
    assert context["reproducibility"]["execution_requirements"] == ["报告成对标准误"]
    assert context["reproducibility"]["accepted_limitations"] == project.accepted_limitations
    assert context["independent_review"]["open_issues"][0]["completion_criteria"] == ["比较解析解与模拟误差"]
    reading = context["adopted_evidence"]["items"][0]["source_reading"]
    assert [r["observation_id"] for r in reading["read_ranges"]] == ["obs-1", "obs-3"]
    assert reading["read_ranges"][0]["end_offset"] == 5
    assert reading["read_ranges"][1]["returned_chars"] == 0
    assert reading["snapshot"]["truncated"] is True
    assert "not full-text reading" in reading["range_note"]
    assert context["execution"]["recent_rounds"][0]["latest_attempt"]["run_id"] == "run-1"
    serialized = json.dumps(context)
    assert "SHOULD_NOT_SEND" not in serialized
    assert project.model_dump_json() == before  # no fabricated receipt/resolution/state changes


@pytest.mark.parametrize("limit", [6000, 12000, 32000])
def test_context_is_bounded_and_explicit_about_omissions(project, limit):
    project.objective = "long objective 中文 \\\" " * 10000
    project.constraints = {f"constraint-{i}": "x" * 20000 for i in range(30)}
    project.revision_feedback = ["feedback" * 10000] * 100
    project.evidence = [project.evidence[0].model_copy(update={"evidence_id": f"EVD-{i}", "summary": "q" * 10000})
                        for i in range(100)]
    context = build_research_context(project, max_chars=limit)
    serialized = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    assert len(serialized) <= limit
    assert context["truncated_sections"]
    assert "truncated" in serialized
    assert "Missing context does not mean work was not done" in context["scope_notice"]
    assert context["adopted_evidence"]["total_adopted_evidence"] == 100
    assert context["adopted_evidence"]["omitted_evidence_count"] == 76


class RecordingClient:
    def __init__(self):
        self.calls = []

    def call(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(value=kwargs["output_model"].model_construct(), metadata=SimpleNamespace())


class RoleSkills:
    def load_for_agent(self, *args):
        return []

    def compose_instructions(self, skills):
        return "Perform this scientific role."


@pytest.mark.parametrize("agent_type", [ResearchDirectorAgent, HypothesisScientistAgent, MethodologistAgent,
    StudyDesignerAgent, AnalystAgent, ReproducibilityEngineerAgent, SkepticalReviewerAgent, ScientificSynthesizerAgent])
def test_actual_role_call_receives_human_feedback_and_shared_instructions(project, agent_type):
    client = RecordingClient()
    agent_type(client=client, skill_loader=RoleSkills()).run(project)
    call, = client.calls
    assert call["payload"]["shared_research_context"]["human_feedback"]["revision_feedback"][0] == project.revision_feedback[-1]
    assert call["payload"]["shared_research_context"]["adopted_evidence"]["total_adopted_evidence"] == 1
    assert SHARED_CONTEXT_INSTRUCTION in call["instructions"]
    assert "Do not agree with an incorrect premise" in call["instructions"]
    assert call["payload"]["output_language"] == "zh-CN"


def test_empty_legacy_project_does_not_claim_no_prior_work():
    context = build_research_context(ResearchProject(title="Legacy", objective="Assess a method"))
    assert context["execution"]["computation_status"] == "not_recorded"
    assert context["research_basis"]["status"] is None
    assert context["human_feedback"]["human_dialogue"] == {"pending": None, "responses": []}
    with pytest.raises(ValueError, match="at least 6000"):
        build_research_context(ResearchProject(title="Small", objective="Test"), max_chars=1000)


def test_dialogue_answer_is_forwarded_without_turning_it_into_evidence(project):
    project.human_dialogue = {
        "pending": {"request_id": "request-2", "prompt": "是否采用配对设计？", "phase": "STUDY_DESIGN"},
        "responses": [{"request_id": "request-1", "prompt": "数据来自哪里？", "response": "请模拟数据，不是实际受试者。",
                       "answered_at": "2026-10-10T12:00:00Z"}],
    }
    client = RecordingClient()
    HypothesisScientistAgent(client=client, skill_loader=RoleSkills()).run(project)
    dialogue = client.calls[0]["payload"]["shared_research_context"]["human_feedback"]["human_dialogue"]
    assert dialogue["responses"][0]["response"] == "请模拟数据，不是实际受试者。"
    assert dialogue["pending"]["request_id"] == "request-2"
    assert len(project.evidence) == 1
    assert "researcher's explicit instructions" in client.calls[0]["instructions"]


def test_long_constraint_keys_cannot_escape_context_budget(project):
    project.constraints = {"long-key" * 10000: "value"}
    context = build_research_context(project, max_chars=6000)
    assert len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) <= 6000
    assert "research_task" in context["truncated_sections"]
