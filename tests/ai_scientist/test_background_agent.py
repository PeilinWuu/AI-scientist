"""Stage mechanics use explicit model/tool doubles; these are NOT live-Qwen acceptance tests."""
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from src.ai_scientist.background_agent import BackgroundAgent, ResearchBasis, BackgroundAction, BasisProposalReview, fingerprint, independent_basis_approved
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import ResearchPhase, SearchCandidate, ReviewResult, ResearchQualityMetrics, RevisionAction
from src.ai_scientist.quality import enrich_evidence_items
from src.ai_scientist.revision_workflow import normalize_revision_plan


def basis(external='required'):
    return ResearchBasis(external_information=external, rationale='这是与当前问题相匹配的资料需求判断。', information_gaps=[],
                         validation_basis=['仍需实际计算并对照解析解'], initial_queries=['a source question'], stopping_rule='信息足够或明确报告未解决的缺口')


@pytest.fixture
def stage(tmp_path, monkeypatch):
    o = ResearchOrchestrator(tmp_path)
    p = o.create_project('求解方程并对照数值误差', research_policy_version=2)
    p.phase = ResearchPhase.BACKGROUND_RESEARCH
    o.store.save(p)
    s = BackgroundAgent(o)
    monkeypatch.setattr(s, 'call', lambda p, schema, *args, **kwargs: BasisProposalReview(
        decision='ready_for_human', rationale='Matches actual observations') if schema == BasisProposalReview else basis())
    s.advance(p)
    return s, p


def authorize(s, p, network=True, actions=12):
    s.authorize(p, p.background_agent['scope_id'], network, actions)


def adopt(s, p, ids, **kwargs):
    return s.adopt(p, ids, scope_id=p.background_agent['scope_id'], proposal_id=p.background_agent['proposal_id'], **kwargs)


def source(p):
    candidate = SearchCandidate(title='Numerical integration', url='https://example.org/paper', query='numerical', rank=1)
    p.background_agent['sources'].append(candidate.model_dump(mode='json'))
    return candidate.candidate_id


def action(kind, **kwargs):
    return BackgroundAction(action=kind, purpose='根据真实观察继续研究', **kwargs)


def test_scope_stops_before_tools_and_is_bound_to_input(stage):
    s, p = stage
    assert p.phase == ResearchPhase.SEARCH_PLAN_REVIEW
    assert not p.background_agent.get('authorization')
    with pytest.raises(ValueError, match='stale'):
        s.authorize(p, 'wrong', True)
    p.objective += '已修改'
    with pytest.raises(ValueError, match='changed'):
        authorize(s, p)


def test_read_assess_adopt_has_real_quote_provenance(stage, monkeypatch):
    s, p = stage
    authorize(s, p)
    sid = source(p)
    quote = 'Euler integration is compared with the exact analytic solution.'
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', lambda url: {
        'url': url, 'text': quote, 'sha256': 'testhash', 'truncated': False})
    keep = action('assess_source', source_id=sid, decision='keep', quote=quote,
                  summary='比较数值误差', relevance='提供数值对照方法', credibility='需注意来源适用范围')
    with pytest.raises(ValueError, match='actual read'):
        s.perform(p, keep)
    s.perform(p, action('read_source', source_id=sid))
    with pytest.raises(ValueError, match='actual read'):
        s.perform(p, keep.model_copy(update={'quote': 'An invented quote which is not in the original text.'}))
    s.perform(p, keep)
    s.perform(p, action('finish', summary='资料支持方法比较，结果仍须计算'))
    assert p.phase == ResearchPhase.HUMAN_SOURCE_REVIEW
    assert p.evidence == []
    with pytest.raises(ValueError, match='stale'):
        s.adopt(p, [sid], scope_id=p.background_agent['scope_id'], proposal_id='old')
    adopt(s, p, [sid])
    assert p.phase == ResearchPhase.CLAIM_EVIDENCE_MAPPING
    assert p.evidence[0].source_quote == quote
    assert p.evidence[0].retrieval_provenance['content_assessed']
    enriched = enrich_evidence_items(p.evidence)[0]
    assert enriched.reliability_score == 0  # no DOI/title-based invented numerical rating
    assert enriched.reliability == '需注意来源适用范围'
    assert s.o.get_project(p.project_id).background_agent['status'] == 'complete'


def test_metadata_can_reject_but_cannot_fake_reading(stage):
    s, p = stage
    authorize(s, p)
    sid = source(p)
    assessment = s.perform(p, action('assess_source', source_id=sid, decision='reject', summary='主题无关'))
    assert assessment['reading_basis'] == 'metadata_only'
    assert not assessment['quote_matched']
    with pytest.raises(ValueError, match='Required external'):
        s.perform(p, action('finish'))


def test_no_external_path_requires_explicit_human_adoption(stage, monkeypatch):
    s, p = stage
    s.feedback(p, '这是自足问题，请重新判断', regenerate=True)
    monkeypatch.setattr(s, 'call', lambda p, schema, *args, **kwargs: BasisProposalReview(
        decision='ready_for_human', rationale='Only proposes future validation') if schema == BasisProposalReview else basis('not_needed'))
    s.advance(p)
    authorize(s, p, network=False)
    s.perform(p, action('finish', summary='采用给定前提与后续计算验证，不引入外部文献。'))
    with pytest.raises(ValueError, match='explicitly confirm'):
        adopt(s, p, [])
    adopt(s, p, [], confirm_basis=True)
    assert independent_basis_approved(p)
    assert p.evidence == [] and p.claims == []
    assert p.background_agent['basis_approval']['validation_is_planned_not_executed']
    p.objective += '不同问题'
    assert not independent_basis_approved(p)


def test_adaptive_loop_observes_failure_and_changes_action(stage, monkeypatch):
    s, p = stage
    authorize(s, p, network=False)
    calls = []
    def model(project, schema, payload):
        calls.append(payload)
        if len(calls) == 1:
            return action('search_web', query='numerical analysis')
        assert payload['observations'][-1]['status'] == 'failed'
        return action('ask_user', summary='尚未授权网络，是否允许检索？', observation_ids=[payload['observations'][-1]['id']])
    monkeypatch.setattr(s, 'call', model)
    s.advance(p)
    assert p.phase == ResearchPhase.HUMAN_SOURCE_REVIEW
    assert p.background_agent['status'] == 'needs_input'
    assert len(p.background_agent['observations']) == 2


def test_budget_and_unknown_observation_do_not_grant_completion(stage, monkeypatch):
    s, p = stage
    authorize(s, p, actions=1)
    monkeypatch.setattr(s, 'call', lambda *args: action('finish', observation_ids=['invented']))
    s.advance(p)
    assert p.phase == ResearchPhase.SEARCH_PLAN_REVIEW
    assert p.background_agent['status'] == 'budget_paused'
    assert p.background_agent['observations'][0]['status'] == 'failed'
    assert p.evidence == []


def test_feedback_regeneration_archives_and_revokes_authorization(stage):
    s, p = stage
    authorize(s, p)
    s.feedback(p, '更换检索方向并重新论证', regenerate=True)
    assert p.background_agent == {'feedback': ['更换检索方向并重新论证']}
    assert any(a.artifact_type == 'background_scope_history' for a in p.artifacts)


def test_legacy_apis_cannot_bypass_new_stage(stage):
    s, p = stage
    for call in [lambda: s.o.approve_search_plan(p.project_id), lambda: s.o.regenerate_search_plan(p.project_id),
                 lambda: s.o.add_human_sources(p.project_id, ['https://example.org']), lambda: s.o.submit_source_selection(p.project_id, [])]:
        with pytest.raises(Exception, match='background'):
            call()


def test_independent_review_rejects_invented_execution_and_model_revises(stage, monkeypatch):
    s, p = stage
    p.background_agent['scope']['external_information'] = 'not_needed'
    authorize(s, p, network=False)
    reviews = []
    def model(project, schema, payload, **kwargs):
        if schema == BasisProposalReview:
            reviews.append(payload)
            if len(reviews) == 1:
                return BasisProposalReview(decision='revise', rationale='没有运行计算，不能报告已验证误差。',
                                           unsupported_completion_claims=['误差已验证'])
            return BasisProposalReview(decision='ready_for_human', rationale='只描述后续验证，不声称执行。')
        if not reviews:
            return action('finish', summary='误差已验证')
        assert payload['observations'][-1]['result']['status'] == 'proposal_requires_revision'
        return action('finish', summary='无需文献；后续仍要执行数值计算并验证误差。',
                      observation_ids=[payload['observations'][-1]['id']])
    monkeypatch.setattr(s, 'call', model)
    s.advance(p)
    assert len(reviews) == 2
    assert p.phase == ResearchPhase.HUMAN_SOURCE_REVIEW and p.evidence == []
    assert p.background_agent['proposal'] == '无需文献；后续仍要执行数值计算并验证误差。'
    assert p.background_agent['observations'][0]['result']['unsupported_completion_claims'] == ['误差已验证']


def test_migration_is_explicit_and_archives_old_work(tmp_path):
    o = ResearchOrchestrator(tmp_path)
    p = o.create_project('Old project')
    p.phase = ResearchPhase.HUMAN_SOURCE_REVIEW
    old = p.background_research_checkpoint.model_dump()
    BackgroundAgent(o).enable(p)
    assert p.background_research_checkpoint.model_dump() == old
    assert p.phase == ResearchPhase.BACKGROUND_RESEARCH and p.research_policy_version == 2
    assert any(a.artifact_type == 'legacy_background_archive' for a in p.artifacts)


def test_model_revision_classification_and_criteria_not_rewritten():
    review = ReviewResult(evidence_quality_score=5, methodological_validity_score=7, feasibility_score=7,
                          reproducibility_score=7, claim_support_score=7, uncertainty_handling_score=7,
                          decision='revise_analysis', revision_plan=[RevisionAction(
                              target='analysis', reason='A domain-specific defect', priority=50,
                              classification='plan_blocking', completion_criteria=['code is archived'])])
    issue = normalize_revision_plan(review, planning_only=True, model_led=True)[0]
    assert issue.classification == 'plan_blocking' and issue.target == 'analysis_plan'
    assert issue.completion_criteria == ['code is archived']


def test_review_policy_artifact_does_not_change_model_review(stage):
    from src.ai_scientist.agents.base_agent import AgentRun
    from tests.ai_scientist.test_human_evidence_curation import metadata
    s, p = stage
    review = ReviewResult(evidence_quality_score=5, methodological_validity_score=7, feasibility_score=7,
                          reproducibility_score=7, claim_support_score=7, uncertainty_handling_score=7, decision='approve')
    before = review.model_dump()
    s.o._record_agent_output(p, ResearchPhase.FEASIBILITY_REVIEW, AgentRun(output=review, metadata=metadata()), 'independent_review')
    assert review.model_dump() == before
    assert p.review_policy_findings[-1]['model_verdict_preserved']
    assert 'reviewer_score_below_6' in p.review_policy_findings[-1]['advisory_signals']


@pytest.mark.parametrize('model_passed', [True, False])
def test_revision_verifier_preserves_semantic_judgment_and_raw_result(stage, monkeypatch, model_passed):
    from src.ai_scientist.agents.base_agent import AgentRun
    from src.ai_scientist.schemas import ApprovedRevisionPlan, RevisionTargetBatch, RevisionCriterionResult, RevisionVerificationResult
    from tests.ai_scientist.test_human_evidence_curation import metadata
    s, p = stage
    batch = RevisionTargetBatch(target='analysis_plan', completion_criteria=['Validate the functional form'])
    approved = ApprovedRevisionPlan(project_id=p.project_id, review_version=1, revision_cycle=1, target_batches=[batch])
    original = RevisionVerificationResult(action_id=batch.batch_id, target_artifact=batch.target, artifact_version=2,
        criteria_results=[RevisionCriterionResult(criterion=batch.completion_criteria[0], passed=True, evidence='Domain-specific derivation')],
        overall_passed=model_passed, verification_method='model')
    monkeypatch.setattr('src.ai_scientist.orchestrator.RevisionVerifierAgent.verify',
                        lambda *args: AgentRun(output=original, metadata=metadata()))
    verified, _ = s.o._verify_revision_batch(p, approved, batch, [], {}, {'derivation': 'No medical keywords here'}, 2)
    assert verified.overall_passed == model_passed
    assert verified.criteria_results == original.criteria_results
    assert any(a.artifact_type == 'model_revision_verification' for a in p.artifacts)
    assert original.verification_method == 'model'


def test_ui_uses_natural_language_and_requires_authorization(stage):
    _, p = stage
    app = AppTest.from_string('''import streamlit as st
from src.ai_scientist.background_agent_ui import render_background_agent
render_background_agent(st.session_state['project'], False, lambda path, payload: st.session_state.update(sent=payload))
''')
    app.session_state['project'] = p.model_dump(mode='json')
    app.run()
    assert not app.exception and not app.json
    assert next(b for b in app.button if b.label == '批准工具范围并继续研究').disabled
    app.checkbox[0].check().run()
    next(b for b in app.button if b.label == '批准工具范围并继续研究').click().run()
    assert app.session_state['sent']['allow_network'] is False
    assert app.session_state['sent']['scope_id'] == p.background_agent['scope_id']


def test_successful_response_without_tool_execution_has_no_candidates(monkeypatch):
    from src.ai_scientist.tools.search_tools import QwenEvidenceSearchTool
    used = {}
    def response(**kwargs):
        used.update(kwargs)
        return {'id': 'real-response-id', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'An unsourced answer.'}]}]}
    client = SimpleNamespace(client=SimpleNamespace(responses=SimpleNamespace(create=response)),
                             http_client=SimpleNamespace(close=lambda: None), base_url='https://example.org')
    client.client.with_options = lambda **kwargs: client.client
    monkeypatch.setattr('src.ai_scientist.tools.search_tools.SearchQwenClient', lambda **kwargs: client)
    result = QwenEvidenceSearchTool().search_query('question', 'qwen-test')
    assert used['tool_choice'] == 'required'
    assert used['extra_body']['enable_thinking'] is False
    assert not result['search_executed'] and result['candidates'] == []
    assert result['response_id'] == 'real-response-id'


def test_background_api_rejects_active_job_and_stale_scope(stage, monkeypatch):
    from fastapi.testclient import TestClient
    from src import main_api
    from src.ai_scientist.job_store import ResearchJobStore
    s, p = stage
    monkeypatch.setattr(main_api, 'research_orchestrator', s.o)
    monkeypatch.setattr(main_api, 'research_job_store', ResearchJobStore(s.o.store.root))
    with TestClient(main_api.app) as client:
        path = f'/api/research/{p.project_id}/background-agent/authorize'
        assert client.post(path, json={'scope_id': 'stale'}).status_code == 400
        main_api.research_job_store.create(p.project_id, p.phase.value)
        assert client.post(path, json={'scope_id': p.background_agent['scope_id']}).status_code == 409
        assert client.post(f'/api/research/{p.project_id}/background-agent/feedback', json={'message': '反馈'}).status_code == 409
