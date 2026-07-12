from pathlib import Path

from src.ai_scientist.agents.base_agent import AgentRun
from src.ai_scientist.agents.evidence_researcher import EvidenceResearcherAgent
from src.ai_scientist.domain_resolution import resolve_domain
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import (
    Claim,
    EvidenceItem,
    EvidenceResearchOutput,
    ResearchMode,
    ResearchPhase,
    ResearchQuestion,
    SearchAcquisitionResult,
)
from src.ai_scientist.skill_loader import SkillLoader
from src.ai_scientist.structured_client import StructuredCallMetadata, StructuredCallResult
from src.ai_scientist.schemas import utc_now


def metadata(agent_name: str = "evidence_researcher") -> StructuredCallMetadata:
    now = utc_now()
    return StructuredCallMetadata(
        agent_name=agent_name,
        requested_model="test-model",
        actual_model="test-model",
        fallback_used=False,
        started_at=now,
        finished_at=now,
        model_calls=1,
        attempted_calls=1,
        successful_calls=1,
    )


def question(text: str) -> ResearchQuestion:
    return ResearchQuestion(
        original_question=text,
        normalized_question=text,
        objective=text,
        scope="theoretical background evidence",
    )


def test_molecular_biology_domain_resolves_to_biology_skill() -> None:
    resolution = resolve_domain("molecular_biology", ["biochemistry", "origin_of_life"])

    assert resolution.reported_primary_domain == "molecular_biology"
    assert resolution.canonical_primary_domain == "biology"
    assert resolution.loaded_domain_skill == "biology"
    assert resolution.canonical_secondary_domains == ["chemistry", "biology"]

    skills = SkillLoader().load_for_agent(
        "evidence_researcher",
        ResearchMode.THEORETICAL,
        resolution.reported_primary_domain,
    )
    assert [item["name"] for item in skills][-1] == "biology"


def test_evidence_item_tolerates_missing_optional_source_fields() -> None:
    item = EvidenceItem.model_validate(
        {
            "title": "Homochirality review",
            "summary": "Summarizes possible roles of molecular handedness.",
            "authors": "Unknown author",
            "publication_date": 2024,
            "limitations": "No direct proof of necessity.",
            "supporting_sources": ["https://example.test/source"],
        }
    )

    assert item.doi is None
    assert item.source_url is None
    assert item.publication_date == "2024"
    assert item.authors == ["Unknown author"]
    assert item.verified is False
    assert item.verification_note == "No verifiable URL was returned."


def test_evidence_normalization_does_not_keep_invented_urls() -> None:
    class FakeClient:
        def call(self, *args, **kwargs):
            output = EvidenceResearchOutput(
                evidence=[
                    EvidenceItem(
                        title="Invented source URL",
                        source_type="paper",
                        source_url="https://not-returned.example/paper",
                        summary="A model-generated evidence summary.",
                    )
                ],
                claims=[
                    Claim(
                        statement="A claim without linked evidence",
                        claim_type="reported_fact",
                        status="supported",
                    )
                ],
            )
            return StructuredCallResult(value=output, metadata=metadata())

    project = type(
        "ProjectLike",
        (),
        {
            "question": question("Does life necessarily require molecular homochirality?"),
            "objective": "Does life necessarily require molecular homochirality?",
            "domain": "molecular_biology",
            "secondary_domains": ["biochemistry"],
            "domain_resolution": resolve_domain("molecular_biology", ["biochemistry"]),
            "research_mode": ResearchMode.THEORETICAL,
            "constraints": {},
            "model_dump": lambda self, mode="json": {
                "question": self.question.model_dump(mode=mode),
                "objective": self.objective,
                "domain": self.domain,
                "secondary_domains": self.secondary_domains,
                "domain_resolution": self.domain_resolution.model_dump(mode=mode),
                "research_mode": self.research_mode.value,
                "constraints": self.constraints,
            },
        },
    )()
    agent = EvidenceResearcherAgent(client=FakeClient())
    acquisition = SearchAcquisitionResult(
        final_text="Search returned background discussion but no explicit citation metadata.",
        sources=[],
        search_used=True,
    )

    run = agent.normalize_search_result(project, acquisition)

    assert run.output.evidence[0].source_url is None
    assert run.output.evidence[0].verified is False
    assert "not returned" in (run.output.evidence[0].verification_note or "")
    assert run.output.claims[0].status == "unknown"


def test_background_research_checkpoint_reaches_claim_mapping(
    tmp_path: Path,
    monkeypatch,
) -> None:
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project(
        "Do life systems necessarily need molecular homochirality?",
    )
    project.phase = ResearchPhase.BACKGROUND_RESEARCH
    project.question = question(project.objective)
    project.research_mode = ResearchMode.THEORETICAL
    project.domain = "molecular_biology"
    project.secondary_domains = ["biochemistry", "origin_of_life"]
    orchestrator.store.save(project)

    class FakeEvidenceResearcher:
        acquire_calls = 0

        def __init__(self, *args, **kwargs):
            pass

        def acquire_search(self, project_arg):
            FakeEvidenceResearcher.acquire_calls += 1
            return SearchAcquisitionResult(
                final_text="Search found discussions of homochirality and alternative biochemical systems.",
                sources=[],
                response_id="resp_test",
                search_used=True,
                warnings=["No explicit source metadata was returned by the search provider."],
            )

        def normalize_search_result(self, project_arg, acquisition):
            assert project_arg.domain == "biology"
            assert project_arg.domain_resolution.loaded_domain_skill == "biology"
            return AgentRun(
                output=EvidenceResearchOutput(
                    evidence=[
                        EvidenceItem(
                            title="Unverified background synthesis",
                            summary="Homochirality may support replication and recognition, but necessity is unresolved.",
                        )
                    ],
                    evidence_gaps=["Direct counterexamples for achiral life systems remain unavailable."],
                ),
                metadata=metadata(),
                tool_names=["web_search", "web_extractor"],
            )

    monkeypatch.setattr("src.ai_scientist.orchestrator.EvidenceResearcherAgent", FakeEvidenceResearcher)

    result = orchestrator.run_next_step(project.project_id)
    updated = orchestrator.get_project(project.project_id)
    events = orchestrator.list_events(project.project_id)

    assert result["current_phase"] == ResearchPhase.CLAIM_EVIDENCE_MAPPING.value
    assert updated.domain == "biology"
    assert updated.background_research_checkpoint.search_completed is True
    assert updated.background_research_checkpoint.normalization_completed is True
    assert updated.background_research_checkpoint.search_artifact_id
    assert FakeEvidenceResearcher.acquire_calls == 1
    assert "search_acquisition_completed" in {event.status for event in events}
    assert "evidence_normalization_completed" in {event.status for event in events}
