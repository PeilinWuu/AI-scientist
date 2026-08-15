from __future__ import annotations

from pathlib import Path

import pytest

from src.ai_scientist.agents.base_agent import AgentRun
from src.ai_scientist.evidence_curation import (
    bind_search_plan,
    compute_question_hash,
    deterministic_plan_relevance,
    validate_checkpoint_binding,
    validate_search_plan_binding,
)
from src.ai_scientist.exceptions import ResearchAssetNotFoundError, StaleSearchPlanError
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import (
    EvidenceItem,
    EvidenceResearchOutput,
    ResearchMode,
    ResearchPhase,
    ResearchQuestion,
    SearchCandidate,
    SearchPlan,
    SourceDecisionInput,
    utc_now,
)
from src.ai_scientist.structured_client import StructuredCallMetadata


QUESTION = "系统评估有氧运动是否能够降低原发性高血压成年人的静息收缩压和舒张压。"


def metadata() -> StructuredCallMetadata:
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


def project(orchestrator: ResearchOrchestrator, objective: str = QUESTION):
    item = orchestrator.create_project(objective, domain_hint="medicine")
    item.phase = ResearchPhase.BACKGROUND_RESEARCH
    item.question = ResearchQuestion(
        original_question=objective,
        normalized_question=objective,
        scope="Adults with primary hypertension; aerobic exercise; resting blood pressure.",
    )
    item.domain = "medicine"
    item.research_mode = ResearchMode.SYSTEMATIC_REVIEW
    orchestrator.store.save(item)
    return item


def test_project_creation_persists_evidence_review_mode(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)

    item = orchestrator.create_project(QUESTION, evidence_review_mode="MANUAL")

    assert item.evidence_review_mode == "MANUAL"
    assert orchestrator.get_project(item.project_id).evidence_review_mode == "MANUAL"


def test_hypertension_plan_rejects_rag_drift_and_accepts_medical_queries(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    item = project(orchestrator)
    bad = bind_search_plan(
        item,
        SearchPlan(
            queries=[
                "retrieval augmented generation LLM",
                "vector database semantic search",
                "hybrid search keyword vector",
                "embedding models text retrieval",
            ]
        ),
        "qwen-test",
    )
    good = bind_search_plan(
        item,
        SearchPlan(
            queries=[
                "aerobic exercise hypertension randomized controlled trial blood pressure",
                "aerobic exercise hypertension systematic review meta-analysis",
            ]
        ),
        "qwen-test",
    )

    assert deterministic_plan_relevance(item, bad)[0] == "irrelevant"
    assert deterministic_plan_relevance(item, good)[0] in {"relevant", "partially_relevant"}


def test_search_plan_and_checkpoint_are_isolated_by_project_and_question(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    rag = project(orchestrator, "RAG hybrid search performance")
    medical = project(orchestrator, QUESTION)
    rag_plan = bind_search_plan(rag, SearchPlan(queries=["RAG hybrid search benchmark"]), "qwen-test")

    with pytest.raises(StaleSearchPlanError):
        validate_search_plan_binding(medical, rag_plan)

    medical.background_research_checkpoint.search_plan = rag_plan
    medical.background_research_checkpoint.project_id = rag.project_id
    medical.background_research_checkpoint.question_hash = rag_plan.question_hash
    medical.background_research_checkpoint.search_plan_id = rag_plan.search_plan_id
    with pytest.raises(StaleSearchPlanError):
        validate_checkpoint_binding(medical)


class FakeCurationResearcher:
    search_calls: list[str] = []
    extracted_ids: list[str] = []
    normalization_returns_evidence = True

    def __init__(self, *args, **kwargs):
        pass

    def plan_search(self, project_arg):
        return AgentRun(
            output=SearchPlan(
                queries=["aerobic exercise hypertension randomized controlled trial blood pressure"],
                target_source_types=["RCT", "systematic review"],
                preferred_databases=["PubMed", "Cochrane"],
                rationale="Direct PICO coverage.",
            ),
            metadata=metadata(),
        )

    def search_one_query(self, query, search_model):
        FakeCurationResearcher.search_calls.append(query)
        return {
            "candidates": [
                SearchCandidate(
                    title="Aerobic exercise hypertension randomized trial",
                    url="https://pubmed.ncbi.nlm.nih.gov/12345678/",
                    query=query,
                    rank=1,
                    source_domain="pubmed.ncbi.nlm.nih.gov",
                    pmid="12345678",
                    snippet="Adults with primary hypertension received aerobic exercise; resting BP was measured.",
                ).model_dump(mode="json"),
                SearchCandidate(
                    title="Vector database embedding benchmark",
                    url="https://example.test/vector-database",
                    query=query,
                    rank=2,
                    source_domain="example.test",
                    snippet="Embedding retrieval benchmark.",
                ).model_dump(mode="json"),
                SearchCandidate(
                    title="Exercise hypertension meta-analysis",
                    url="https://doi.org/10.1000/example",
                    query=query,
                    rank=3,
                    source_domain="doi.org",
                    doi="10.1000/example",
                    snippet="Systematic review of aerobic exercise and blood pressure.",
                ).model_dump(mode="json"),
            ]
        }

    def extract_candidate_batch(self, candidates, search_model, progress_callback=None):
        FakeCurationResearcher.extracted_ids.extend(item.candidate_id for item in candidates)
        return {"final_text": "Selected source extraction."}

    def normalize_search_result(self, project_arg, acquisition):
        if not FakeCurationResearcher.normalization_returns_evidence:
            return AgentRun(output=EvidenceResearchOutput(), metadata=metadata())
        selected = acquisition.selected_candidates[0]
        return AgentRun(
            output=EvidenceResearchOutput(
                evidence=[
                    EvidenceItem(
                        title=selected.title,
                        source_type="paper",
                        source_url=selected.url,
                        pmid=selected.pmid,
                        doi=selected.doi,
                        summary="Reports resting blood-pressure outcomes.",
                    )
                ]
            ),
            metadata=metadata(),
        )


def test_assisted_flow_stops_twice_and_extracts_only_human_kept_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    item = project(orchestrator)
    FakeCurationResearcher.search_calls = []
    FakeCurationResearcher.extracted_ids = []
    FakeCurationResearcher.normalization_returns_evidence = True
    monkeypatch.setattr("src.ai_scientist.orchestrator.EvidenceResearcherAgent", FakeCurationResearcher)

    first = orchestrator.run_next_step(item.project_id)
    assert first["current_phase"] == ResearchPhase.SEARCH_PLAN_REVIEW.value
    assert FakeCurationResearcher.search_calls == []
    stored = orchestrator.get_project(item.project_id)
    assert stored.background_research_checkpoint.search_plan.project_id == item.project_id
    assert stored.background_research_checkpoint.search_plan.question_hash == compute_question_hash(stored)

    orchestrator.approve_search_plan(item.project_id)
    second = orchestrator.run_next_step(item.project_id)
    assert second["current_phase"] == ResearchPhase.HUMAN_SOURCE_REVIEW.value
    assert FakeCurationResearcher.extracted_ids == []
    candidates = orchestrator.get_project(item.project_id).background_research_checkpoint.candidates
    assert len(candidates) == 3

    decisions = [
        SourceDecisionInput(candidate_id=candidates[0].candidate_id, decision="keep"),
        SourceDecisionInput(candidate_id=candidates[1].candidate_id, decision="defer"),
        SourceDecisionInput(
            candidate_id=candidates[2].candidate_id,
            decision="reject",
            rejection_reason="主题无关",
        ),
    ]
    # Identify by title because deterministic sorting can reorder the provider results.
    for decision in decisions:
        candidate = next(item for item in candidates if item.candidate_id == decision.candidate_id)
        if "Aerobic exercise hypertension randomized" in candidate.title:
            decision.decision = "keep"
        elif "Vector database" in candidate.title:
            decision.decision = "reject"
            decision.rejection_reason = "主题无关"
        else:
            decision.decision = "defer"
    orchestrator.submit_source_selection(item.project_id, decisions, "保留直接匹配 PICO 的试验")
    kept_id = next(
        source.candidate_id for source in candidates if "randomized trial" in source.title
    )
    third = orchestrator.run_next_step(item.project_id)
    updated = orchestrator.get_project(item.project_id)

    assert third["current_phase"] == ResearchPhase.CLAIM_EVIDENCE_MAPPING.value
    assert FakeCurationResearcher.extracted_ids == [kept_id]
    assert updated.source_selection_snapshots[-1].created_by == "human"
    assert updated.evidence[0].selection_provenance.candidate_id == kept_id
    assert updated.evidence[0].selection_provenance.selected_by == "human"


def test_human_sources_accept_doi_pmid_url_and_assets_are_locally_parsed(
    tmp_path: Path,
) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    item = project(orchestrator)
    item.phase = ResearchPhase.HUMAN_SOURCE_REVIEW
    plan = bind_search_plan(item, SearchPlan(queries=["aerobic exercise hypertension"]), "qwen-test")
    item.background_research_checkpoint.search_plan = plan
    item.background_research_checkpoint.project_id = item.project_id
    item.background_research_checkpoint.question_hash = plan.question_hash
    item.background_research_checkpoint.search_plan_id = plan.search_plan_id
    orchestrator.store.save(item)

    updated = orchestrator.add_human_sources(
        item.project_id,
        ["10.1000/example", "PMID: 12345678", "https://example.org/paper"],
    )
    assert {source.doi for source in updated.background_research_checkpoint.candidates if source.doi} == {"10.1000/example"}
    assert {source.pmid for source in updated.background_research_checkpoint.candidates if source.pmid} == {"12345678"}
    assert any(source.url == "https://example.org/paper" for source in updated.background_research_checkpoint.candidates)

    updated = orchestrator.register_research_asset(item.project_id, "paper.txt", "text/plain", b"text")
    assert updated.research_assets[-1].parsing_status == "parsed"
    assert updated.research_assets[-1].parsed_artifact_id


def test_research_assets_support_reference_and_data_upload_contexts(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    item = project(orchestrator)

    updated = orchestrator.register_research_asset(
        item.project_id,
        "observations.csv",
        "text/csv",
        b"sample,value\na,1\n",
        purpose="data",
        description="Researcher-provided pilot observations.",
        upload_context="project_creation",
    )

    asset = updated.research_assets[-1]
    assert asset.filename == "observations.csv"
    assert asset.purpose == "data"
    assert asset.description == "Researcher-provided pilot observations."
    assert asset.upload_context == "project_creation"
    assert asset.parsing_status == "parsed"
    assert asset.parsed_content is not None
    assert asset.parsed_content.structured_summary["column_names"] == ["sample", "value"]
    assert (tmp_path / item.project_id / asset.saved_path).read_bytes() == b"sample,value\na,1\n"
    assert orchestrator.list_events(item.project_id)[-1].status == "research_asset_parsed"


def test_research_asset_rejects_unsupported_or_oversized_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    item = project(orchestrator)

    with pytest.raises(ValueError, match="Unsupported research asset type"):
        orchestrator.register_research_asset(
            item.project_id, "program.exe", "application/octet-stream", b"binary"
        )

    monkeypatch.setenv("AI_SCIENTIST_MAX_ASSET_BYTES", "4")
    with pytest.raises(ValueError, match="exceeds"):
        orchestrator.register_research_asset(
            item.project_id, "large.pdf", "application/pdf", b"12345"
        )


def test_research_asset_open_path_is_project_scoped(tmp_path: Path) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    item = project(orchestrator)
    updated = orchestrator.register_research_asset(
        item.project_id, "notes.txt", "text/plain", b"auditable notes"
    )
    asset = updated.research_assets[-1]

    resolved_asset, resolved_path = orchestrator.get_research_asset(item.project_id, asset.asset_id)
    assert resolved_asset.asset_id == asset.asset_id
    assert resolved_path.read_bytes() == b"auditable notes"

    with pytest.raises(ResearchAssetNotFoundError, match="not found"):
        orchestrator.get_research_asset(item.project_id, "asset_missing")

    updated.research_assets[-1].saved_path = "../outside.txt"
    orchestrator.store.save(updated)
    with pytest.raises(ResearchAssetNotFoundError, match="unavailable"):
        orchestrator.get_research_asset(item.project_id, asset.asset_id)


def test_zero_evidence_returns_to_source_review_and_claim_mapper_is_not_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    item = project(orchestrator)
    FakeCurationResearcher.search_calls = []
    FakeCurationResearcher.extracted_ids = []
    FakeCurationResearcher.normalization_returns_evidence = False
    monkeypatch.setattr("src.ai_scientist.orchestrator.EvidenceResearcherAgent", FakeCurationResearcher)
    orchestrator.run_next_step(item.project_id)
    orchestrator.approve_search_plan(item.project_id)
    orchestrator.run_next_step(item.project_id)
    candidates = orchestrator.get_project(item.project_id).background_research_checkpoint.candidates
    orchestrator.submit_source_selection(
        item.project_id,
        [SourceDecisionInput(candidate_id=candidates[0].candidate_id, decision="keep")],
    )
    before = orchestrator.get_project(item.project_id).budget.used_model_calls
    result = orchestrator.run_next_step(item.project_id)
    updated = orchestrator.get_project(item.project_id)
    assert result["current_phase"] == ResearchPhase.HUMAN_SOURCE_REVIEW.value
    assert updated.evidence == []
    assert all("evidence_none" not in item.model_dump_json() for item in [updated])
    assert updated.budget.used_model_calls == before + 2  # extraction + normalization, no claim mapper

    updated.phase = ResearchPhase.CLAIM_EVIDENCE_MAPPING
    orchestrator.store.save(updated)
    monkeypatch.setattr(
        orchestrator,
        "_run_claim_mapping",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("claim mapper must not run")),
    )
    calls_before = orchestrator.get_project(item.project_id).budget.used_model_calls
    claim_result = orchestrator.run_next_step(item.project_id)
    assert claim_result["current_phase"] == ResearchPhase.HUMAN_SOURCE_REVIEW.value
    assert orchestrator.get_project(item.project_id).budget.used_model_calls == calls_before
