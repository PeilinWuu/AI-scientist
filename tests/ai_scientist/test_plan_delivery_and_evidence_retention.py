"""Regression tests for the ordinary-user tree-shade run (model doubles)."""
import pytest

from src.ai_scientist.agents.base_agent import AgentRun
from src.ai_scientist.agents.scientific_synthesizer import ScientificSynthesizerAgent
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.research_context import adopted_source_passages, preserve_adopted_read_receipts
from src.ai_scientist.research_dialogue import allowed_actions
from src.ai_scientist.schemas import (
    Conclusion, EvidenceItem, MethodologyOutput, ResearchMode, ResearchPhase, ResearchQuestion, StudyDesign,
)
from tests.ai_scientist.test_background_agent import stage, adopt, authorize, action, source
from tests.ai_scientist.test_models_structured_api import fake_metadata
from tests.ai_scientist.test_shared_research_context import RecordingClient, RoleSkills
from tests.ai_scientist.test_unified_research_dialogue import model, send
from tests.test_conversation_api import api


def prior_evidence(p):
    p.evidence = [EvidenceItem(evidence_id='EVD-007', title='Previously adopted study', summary='Fallible summary',
        source_url='https://example.org/original', source_quote='The study measured globe temperature, not air temperature.')]
    p.background_agent['sources'].append({'candidate_id': 'source-original', 'source_alias': 'S1',
        'url': p.evidence[0].source_url,
        'shown_excerpts': [p.evidence[0].source_quote], 'retrieval_snapshot': {'sha256': 'original-hash'}})
    preserve_adopted_read_receipts(p)
    return p.evidence[0].model_dump(mode='json')


@pytest.mark.parametrize('required', [True, False])
def test_no_new_sources_preserves_existing_evidence_and_original_passages(stage, required):
    s, p = stage
    previous = prior_evidence(p)
    # A supplementary round can finish without any new sources, even if literature is required.
    p.background_agent['sources'] = []
    p.background_agent['scope']['external_information'] = 'required' if required else 'optional'
    p.background_agent.update(status='awaiting_adoption', proposal_id='proposal')
    p.phase = ResearchPhase.HUMAN_SOURCE_REVIEW
    with pytest.raises(ValueError, match='explicitly confirm'):
        adopt(s, p, [])
    adopt(s, p, [], confirm_basis=True)
    assert [e.model_dump(mode='json') for e in p.evidence] == [previous]
    assert p.background_agent['basis_approval']['without_new_sources']
    assert not p.background_agent['basis_approval']['without_external_sources']
    assert adopted_source_passages(p)['sources'][0]['actual_read_passages'] == [previous['source_quote']]


def test_append_sources_keeps_old_ids_and_duplicate_readoption_is_not_duplicated(stage, monkeypatch):
    s, p = stage
    old = prior_evidence(p)
    authorize(s, p)
    sid = source(p)
    quote = 'The new source describes a different outcome and a different control group.'
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', lambda url: {
        'url': url, 'text': quote, 'sha256': 'new-hash', 'truncated': False})
    s.perform(p, action('read_source', source_id=sid))
    s.perform(p, action('assess_source', source_id=sid, decision='keep', quote=quote,
                        summary='New result', relevance='Relevant', credibility='Limited'))
    s.perform(p, action('finish', summary='Ready for human adoption'))
    adopt(s, p, [sid])
    assert len(p.evidence) == 2 and p.evidence[0].evidence_id == old['evidence_id']
    assert len({e.evidence_id for e in p.evidence}) == 2
    p.phase = ResearchPhase.HUMAN_SOURCE_REVIEW
    p.background_agent['status'] = 'awaiting_adoption'
    adopt(s, p, [sid])
    assert len(p.evidence) == 2
    assert all(e.retrieval_provenance['read_receipt']['shown_excerpts'] for e in p.evidence)


def test_rollback_keeps_read_passages_and_does_not_copy_unread_full_text(stage):
    s, p = stage
    prior_evidence(p)
    p.evidence[0].retrieval_provenance.pop('read_receipt')
    p.background_agent['sources'][0]['extracted_text'] = 'UNREAD FULL BODY'
    s.o._targeted_rollback(p, 'evidence', 'Supplement the same question', [])
    assert not p.background_agent
    context = adopted_source_passages(p)
    assert context['sources'][0]['actual_read_passages'] == [p.evidence[0].source_quote]
    assert 'UNREAD FULL BODY' not in str(context)


def test_synthesizer_receives_receipts_even_after_search_round_reset(stage):
    _, p = stage
    prior_evidence(p)
    p.background_agent = {}
    client = RecordingClient()
    ScientificSynthesizerAgent(client=client, skill_loader=RoleSkills()).run(p)
    payload = client.calls[0]['payload']
    assert payload['adopted_source_read_receipts']['sources'][0]['actual_read_passages']
    assert 'read_receipt' not in payload['evidence'][0]['retrieval_provenance']  # No duplicated bodies.


@pytest.mark.parametrize('delivery,initial,expected', [
    ('plan_only', False, True), ('research_results', False, False),
    ('research_results', True, True), (None, False, False),
])
def test_delivery_scope_is_separate_from_mixed_research_method(tmp_path, monkeypatch, delivery, initial, expected):
    o = ResearchOrchestrator(tmp_path)
    p = o.create_project('Explain an environmental mechanism and design a future campus experiment', planning_only=initial)
    p.question = ResearchQuestion(original_question=p.objective, normalized_question=p.objective)
    p.phase = ResearchPhase.RESEARCH_MODE_SELECTION
    o.store.save(p)
    monkeypatch.setattr(o, '_run_agent', lambda *args: AgentRun(output=MethodologyOutput(
        selected_research_mode=ResearchMode.MIXED_METHODS, methodological_rationale='Appropriate methods',
        delivery_scope=delivery, delivery_scope_rationale='Requested deliverable, not a keyword rule'),
        metadata=fake_metadata('methodologist')))
    o.run_next_step(p.project_id)
    p = o.get_project(p.project_id)
    assert p.planning_only is expected
    assert p.research_mode == ResearchMode.MIXED_METHODS
    assert p.execution_capability == ('PLANNING_ONLY' if expected else 'EXTERNAL_EXECUTION_REQUIRED')


def waiting_plan(api):
    p = api.project
    p.planning_only = False
    p.research_mode = ResearchMode.MIXED_METHODS
    p.phase = ResearchPhase.EXECUTION_WAITING
    p.study_design = StudyDesign(research_mode=p.research_mode, objective='Future experiment')
    p.approval_status = 'valid'
    p.approval_valid_for_versions = api.o._review_artifact_versions(p)
    p.human_actions_required = ['Measure for five days']
    api.o.store.save(p)
    return p


def test_natural_language_plan_delivery_requires_confirmation_then_reaches_real_completion(api):
    p = waiting_plan(api)
    model(api, 'deliver_plan', instructions='Deliver existing explanation and design, no new measurements')
    send(api, '只要资料解释与方案，请结束本轮')
    api.threads[-1].run()
    p = api.o.get_project(p.project_id)
    assert p.phase == ResearchPhase.EXECUTION_WAITING and not p.planning_only
    proposal = p.human_dialogue['proposal']
    result = api.client.post(f'/api/research/{p.project_id}/message/confirm',
                            json={'proposal_id': proposal['id'], 'accept': True})
    assert result.status_code == 200 and result.json().get('job_id')
    p = api.o.get_project(p.project_id)
    assert p.planning_only and p.phase == ResearchPhase.SYNTHESIS
    assert not p.human_actions_required
    assert p.human_dialogue['deferred_execution'][0]['human_actions_required'] == ['Measure for five days']
    api.monkeypatch.setattr(api.o, '_run_agent', lambda *a: AgentRun(
        output=Conclusion(tentative_inferences=['Future measurements are not completed results']),
        metadata=fake_metadata('scientific_synthesizer')))
    api.threads[-1].run()
    p = api.o.get_project(p.project_id)
    assert p.phase == ResearchPhase.COMPLETED and p.conclusion
    assert p.human_dialogue['messages'][0]['status'] == 'applied'
    assert not p.internal_execution_summary and not p.controlled_python_runs
    assert any(a.artifact_type == 'research_plan' for a in p.artifacts)


def test_plan_delivery_does_not_silently_approve_design(api):
    p = waiting_plan(api)
    p.phase = ResearchPhase.HUMAN_APPROVAL
    p.approval_status = 'not_requested'
    api.o.store.save(p)
    model(api, 'deliver_plan')
    send(api)
    api.threads[-1].run()
    p = api.o.get_project(p.project_id)
    result = api.client.post(f'/api/research/{p.project_id}/message/confirm',
        json={'proposal_id': p.human_dialogue['proposal']['id'], 'accept': True})
    assert result.status_code == 200
    p = api.o.get_project(p.project_id)
    assert p.planning_only and p.phase == ResearchPhase.HUMAN_APPROVAL
    assert p.approval_status != 'valid'


def test_stale_approval_and_actual_execution_cannot_be_relabelled_as_plan(api):
    p = waiting_plan(api)
    p.approval_status = 'stale'
    assert 'deliver_plan' not in allowed_actions(p)
    p.approval_status = 'valid'
    p.internal_execution_summary = {'status': 'complete'}
    assert 'deliver_plan' not in allowed_actions(p)


def test_dialogue_receives_read_passages_not_just_previous_answers(api):
    p = waiting_plan(api)
    p.background_agent['sources'] = []
    prior_evidence(p)
    p.background_agent = {}
    api.o.store.save(p)
    from src.ai_scientist.background_agent import BackgroundAgent
    from src.ai_scientist.research_dialogue import DialogueDecision
    def check(self, project, schema, payload, **kwargs):
        assert payload['adopted_source_read_receipts']['sources'][0]['actual_read_passages'] == [p.evidence[0].source_quote]
        return DialogueDecision(action='discuss', public_response='Earlier outcome attribution was incorrect.')
    api.monkeypatch.setattr(BackgroundAgent, 'call', check)
    send(api, '这篇文章真测了空气温度吗？')
    api.threads[-1].run()
    p = api.o.get_project(p.project_id)
    assert p.human_dialogue['messages'][0]['status'] == 'answered'


def test_evidence_repair_is_explicit_backed_up_and_invalidates_approval(stage):
    from scripts.repair_retained_evidence import repair
    s, p = stage
    prior_evidence(p)
    p.background_agent.update(adopted_source_ids=['source-original'], basis_approval={'confirmed_at': 'earlier'})
    evidence_id = s.record(p, 'evidence_map', {'evidence': [e.model_dump(mode='json') for e in p.evidence]})
    archive_id = s.record(p, 'background_rollback_archive', p.background_agent)
    evidence_file = next(a.filename for a in p.artifacts if a.artifact_id == evidence_id)
    archive_file = next(a.filename for a in p.artifacts if a.artifact_id == archive_id)
    p.evidence = []
    p.background_agent = {'basis_approval': {'without_external_sources': True}}
    p.phase = ResearchPhase.EXECUTION_WAITING
    p.approval_status = 'valid'
    s.o.store.save(p)
    before = s.o.store.load(p.project_id).model_dump_json()
    result = repair(s.o, p.project_id, evidence_file, archive_file)
    assert not result['applied'] and result['restored_count'] == 1
    assert s.o.store.load(p.project_id).model_dump_json() == before
    repair(s.o, p.project_id, evidence_file, archive_file, apply=True)
    saved = s.o.store.load(p.project_id)
    assert len(saved.evidence) == 1 and saved.approval_status == 'stale'
    assert saved.phase == ResearchPhase.CLAIM_EVIDENCE_MAPPING
    assert saved.human_dialogue['evidence_link_repair']['repair_artifact_id']
    assert any(a.artifact_type == 'before_evidence_repair' for a in saved.artifacts)
    assert saved.evidence[0].retrieval_provenance['read_receipt']
    with pytest.raises(ValueError, match='empty evidence'):
        repair(s.o, p.project_id, evidence_file, archive_file, apply=True)


def test_plan_approval_does_not_present_future_measurements_as_this_round_deliverables():
    from src.ai_scientist.experience_ui import approval_summary
    summary = dict(approval_summary({'planning_only': True, 'objective': 'Explain cooling',
        'study_design': {'sampling_plan': ['Measure five days'], 'stopping_rules': ['Wait for field data']},
        'analysis_plan': {'visualization_plan': ['A chart from real data']}}))
    assert '不等待实验数据' in summary['本轮交付']
    assert summary['未来实验建议'] == 'Measure five days'
    assert '停止条件' not in summary and '交付内容' not in summary


def test_same_download_more_reading_does_not_erase_earlier_pages(stage):
    _, p = stage
    prior_evidence(p)
    original = p.evidence[0].source_quote
    p.background_agent['sources'][0]['shown_excerpts'] = ['Another actually read page.']
    preserve_adopted_read_receipts(p)
    assert adopted_source_passages(p)['sources'][0]['actual_read_passages'] == [original, 'Another actually read page.']


@pytest.mark.parametrize('registered', [True, False])
def test_recovered_links_go_to_review_only_with_registered_repair(stage, monkeypatch, registered):
    from scripts.repair_retained_evidence import _register_link_repair
    from src.ai_scientist.schemas import ClaimEvidenceMappingResult
    s, p = stage
    prior_evidence(p)
    p.study_design = StudyDesign(research_mode=ResearchMode.MIXED_METHODS, objective='Existing design')
    p.phase = ResearchPhase.CLAIM_EVIDENCE_MAPPING
    if registered:
        _register_link_repair(s.o, p)
    else:
        p.human_dialogue['evidence_link_repair'] = {'repair_artifact_id': 'not-a-real-repair', 'evidence_version': None}
    s.o.store.save(p)
    monkeypatch.setattr(s.o, '_run_claim_mapping', lambda *a: (
        ClaimEvidenceMappingResult(claims=[{'claim_id': 'c1', 'statement': p.evidence[0].source_quote,
            'claim_type': 'reported_fact', 'importance': 'high', 'status': 'supported',
            'supporting_evidence_ids': ['EVD-007']}]), fake_metadata('evidence_researcher')))
    s.o.run_next_step(p.project_id)
    saved = s.o.get_project(p.project_id)
    assert saved.phase == (ResearchPhase.CRITICAL_REVIEW if registered else ResearchPhase.HYPOTHESIS_GENERATION)
    assert saved.claims[0].supporting_evidence_ids == ['EVD-007']
    assert saved.phase != ResearchPhase.COMPLETED


def test_missing_internal_links_do_not_get_mislabelled_as_scientifically_unsupported(stage):
    from types import SimpleNamespace
    from src.ai_scientist.schemas import ConclusionItem
    from src.ai_scientist.report_writer import build_research_plan_markdown
    _, p = stage
    statement = 'A source-specific finding with no internal claim link yet.'
    class Client:
        def call(self, **kwargs):
            return SimpleNamespace(value=Conclusion(supported_findings=[ConclusionItem(statement=statement)]),
                                   metadata=SimpleNamespace())
    result = ScientificSynthesizerAgent(client=Client(), skill_loader=RoleSkills()).run(p)
    assert not result.output.supported_findings  # Still cannot claim verified support.
    assert result.output.traceability_pending_findings == [statement]
    assert statement not in result.output.unsupported_claims
    p.conclusion = result.output
    report = build_research_plan_markdown(p)
    assert statement in report and '引用关联待核对（不等于结论为假）' in report
