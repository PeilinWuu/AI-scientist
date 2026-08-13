"""Regression coverage for bounded, resumable Evidence Researcher acquisition."""

from __future__ import annotations

from pathlib import Path

from src.ai_scientist.agents.base_agent import AgentRun
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import (
    EvidenceItem,
    EvidenceResearchOutput,
    ResearchMode,
    ResearchPhase,
    ResearchQuestion,
    SearchCandidate,
    SearchPlan,
    SearchQueryRecord,
    utc_now,
)
from src.ai_scientist.source_selector import select_sources
from src.ai_scientist.structured_client import StructuredCallMetadata


BENCHMARK_QUESTION = (
    "系统评估有氧运动是否能够降低原发性高血压成年人的静息收缩压和舒张压，"
    "并比较不同运动频率、强度和持续时间的影响。"
)


def _metadata() -> StructuredCallMetadata:
    now = utc_now()
    return StructuredCallMetadata(
        agent_name="evidence_researcher",
        requested_model="qwen-test",
        actual_model="qwen-test",
        fallback_used=False,
        started_at=now,
        finished_at=now,
        model_calls=1,
        attempted_calls=1,
        successful_calls=1,
    )


def _project(orchestrator: ResearchOrchestrator):
    project = orchestrator.create_project(
        BENCHMARK_QUESTION,
        domain_hint="medicine",
        constraints={"preferred_sources": ["RCT", "systematic review", "meta-analysis", "PubMed", "Cochrane"]},
        planning_only=True,
    )
    project.phase = ResearchPhase.BACKGROUND_RESEARCH
    project.question = ResearchQuestion(
        original_question=BENCHMARK_QUESTION,
        normalized_question=BENCHMARK_QUESTION,
        objective=BENCHMARK_QUESTION,
        scope="Adults with primary hypertension; systematic review planning only.",
    )
    project.research_mode = ResearchMode.SYSTEMATIC_REVIEW
    project.domain = "medicine"
    orchestrator.store.save(project)
    return project


def test_search_plan_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv("AI_SCIENTIST_MAX_SEARCH_QUERIES", "4")
    plan = SearchPlan(
        queries=[f"query {index}" for index in range(8)],
        maximum_queries=8,
        target_source_types=["RCT", "systematic review"],
        preferred_databases=["PubMed", "Cochrane"],
        rationale="Bounded benchmark plan.",
    )

    assert len(plan.queries) == 4
    assert plan.maximum_queries == 4


def test_partial_query_timeout_and_extraction_failure_still_advance(
    tmp_path: Path, monkeypatch
) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = _project(orchestrator)

    class FakeEvidenceResearcher:
        query_calls: list[str] = []
        extraction_calls = 0

        def __init__(self, *args, **kwargs):
            pass

        def plan_search(self, project_arg):
            return AgentRun(
                output=SearchPlan(
                    queries=["aerobic hypertension RCT", "exercise blood pressure meta analysis", "Cochrane exercise hypertension"],
                    maximum_queries=3,
                    target_source_types=["RCT", "systematic review", "meta-analysis"],
                    preferred_databases=["PubMed", "Cochrane"],
                    rationale="Benchmark",
                ),
                metadata=_metadata(),
            )

        def search_one_query(self, query, search_model):
            self.query_calls.append(query)
            if "meta analysis" in query:
                raise TimeoutError("one bounded query timed out")
            return {
                "candidates": [
                    SearchCandidate(
                        title=f"Study {index} for {query}",
                        url=f"https://pubmed.ncbi.nlm.nih.gov/{len(self.query_calls)}00{index}",
                        query=query,
                        rank=index,
                        source_domain="pubmed.ncbi.nlm.nih.gov",
                    ).model_dump(mode="json")
                    for index in range(1, 4)
                ]
            }

        def extract_candidate_batch(self, candidates, search_model, progress_callback=None):
            FakeEvidenceResearcher.extraction_calls += 1
            if progress_callback:
                progress_callback("response.created")
            if FakeEvidenceResearcher.extraction_calls == 2:
                raise TimeoutError("one extraction batch timed out")
            return {"final_text": "RCT metadata and bounded outcome extraction."}

        def normalize_search_result(self, project_arg, acquisition):
            assert acquisition.query_records[1].status == "timeout"
            return AgentRun(
                output=EvidenceResearchOutput(
                    evidence=[
                        EvidenceItem(
                            title="Aerobic exercise RCT",
                            source_type="paper",
                            source_url=acquisition.selected_candidates[0].url,
                            summary="Reports resting blood-pressure outcomes.",
                        )
                    ],
                    evidence_gaps=["Dose-response evidence remains incomplete."],
                ),
                metadata=_metadata(),
            )

    monkeypatch.setattr("src.ai_scientist.orchestrator.EvidenceResearcherAgent", FakeEvidenceResearcher)

    result = orchestrator.run_next_step(project.project_id)
    updated = orchestrator.get_project(project.project_id)

    assert result["current_phase"] == ResearchPhase.CLAIM_EVIDENCE_MAPPING.value
    assert updated.phase == ResearchPhase.CLAIM_EVIDENCE_MAPPING
    assert any(item.status == "timeout" for item in updated.background_research_checkpoint.query_records)
    assert updated.background_research_checkpoint.normalization_completed is True
    statuses = {item.extraction_status for item in updated.background_research_checkpoint.selected_candidates}
    assert "completed" in statuses
    assert "timeout" in statuses
    assert any(item.artifact_type == "search_checkpoint" for item in updated.artifacts)


def test_completed_checkpoint_query_is_not_repeated(tmp_path: Path, monkeypatch) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = _project(orchestrator)
    query = "completed PubMed query"
    project.background_research_checkpoint.search_plan = SearchPlan(
        queries=[query], maximum_queries=1, rationale="resume"
    )
    project.background_research_checkpoint.query_records = [
        SearchQueryRecord(query=query, status="completed", candidate_count=1)
    ]
    project.background_research_checkpoint.candidates = [
        SearchCandidate(
            title="Saved RCT",
            url="https://pubmed.ncbi.nlm.nih.gov/12345",
            query=query,
            rank=1,
            source_domain="pubmed.ncbi.nlm.nih.gov",
        )
    ]
    orchestrator.store.save(project)

    class ResumeEvidenceResearcher:
        def __init__(self, *args, **kwargs):
            pass

        def search_one_query(self, query, search_model):
            raise AssertionError("completed query must not be repeated")

        def extract_candidate_batch(self, candidates, search_model, progress_callback=None):
            return {"final_text": "Saved RCT extraction."}

        def normalize_search_result(self, project_arg, acquisition):
            return AgentRun(output=EvidenceResearchOutput(), metadata=_metadata())

    monkeypatch.setattr("src.ai_scientist.orchestrator.EvidenceResearcherAgent", ResumeEvidenceResearcher)

    result = orchestrator.run_next_step(project.project_id)

    assert result["current_phase"] == ResearchPhase.CLAIM_EVIDENCE_MAPPING.value


def test_source_selection_deduplicates_and_prioritizes_formal_records() -> None:
    candidates = [
        SearchCandidate(
            title="Exercise and blood pressure trial",
            url="https://pubmed.ncbi.nlm.nih.gov/12345?utm_source=test",
            query="q1",
            rank=2,
            source_domain="pubmed.ncbi.nlm.nih.gov",
        ),
        SearchCandidate(
            title="Exercise and blood pressure trial",
            url="https://pubmed.ncbi.nlm.nih.gov/12345",
            query="q2",
            rank=1,
            source_domain="pubmed.ncbi.nlm.nih.gov",
        ),
        SearchCandidate(
            title="Exercise news summary",
            url="https://example-news.test/blog/exercise",
            query="q1",
            rank=1,
            source_domain="example-news.test",
        ),
    ]

    selected = select_sources(candidates, maximum=8)

    assert len(selected) == 2
    assert selected[0].url == "https://pubmed.ncbi.nlm.nih.gov/12345"
