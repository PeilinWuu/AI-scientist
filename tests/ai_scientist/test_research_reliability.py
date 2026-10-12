"""Long-context/state regressions. Mock tests do NOT certify scientific model quality."""
import json
from copy import deepcopy
import pytest
from src.ai_scientist.background_agent import BackgroundAgent, BackgroundAction, SourceAssessmentReview, AuditIssue
from src.ai_scientist.background_memory import action_payload
from src.ai_scientist.continuous_research import progress_signature
from src.ai_scientist.schemas import ResearchPhase, ResearchMode, EvidenceItem
from src.ai_scientist.structured_client import StructuredQwenClient, _call_context, MarkdownDraft
from src.ai_scientist.literature_execution import run_review, EvidenceRow, LiteratureOutput, LiteratureReadiness, audit_synthesis, synthesis_rows
from tests.ai_scientist.test_background_agent import stage, authorize, source, action


def test_search_event_does_not_mutate_after_read(stage, monkeypatch):
    s, p = stage
    authorize(s, p)
    from src.ai_scientist.schemas import SearchCandidate
    c = SearchCandidate(title='A source', url='https://example.org/a', query='a question', rank=1)
    monkeypatch.setattr('src.ai_scientist.background_agent.QwenEvidenceSearchTool.search_query',
        lambda *a: {'search_executed': True, 'candidates': [c.model_dump(mode='json')]})
    found = s.perform(p, action('search_web', query='a question'))
    before = deepcopy(found)
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', lambda url:
        {'url': url, 'text': 'Actual original scientific text. ' * 10000, 'sha256': 'hash'})
    s.perform(p, action('read_source', source_id=c.candidate_id))
    assert found == before
    assert 'extracted_text' not in found['candidates'][0]


def test_legacy_bloated_events_are_not_sent_and_old_reads_survive(stage):
    _, p = stage
    text = 'Original research paragraph. ' * 20000
    p.background_agent['observations'] = [
        {'id': 'search', 'action': 'search_web', 'status': 'completed', 'result': {
            'candidates': [{'extracted_text': text, 'retrieval_snapshot': {'text': text}}]}},
        {'id': 'read', 'action': 'read_source', 'status': 'completed',
         'result': {'source_id': 'S21', 'offset': 0, 'text': text[:12000]}}]
    p.background_agent['observations'] += [
        {'id': f'failed{i}', 'action': 'assess_source', 'status': 'failed',
         'observation_ids': ['read'], 'result': {'error': 'quote mismatch'}} for i in range(20)]
    payload = action_payload(p)
    assert len(json.dumps(payload)) < 23000
    assert 'read' in {x['id'] for x in payload['observation_index']}
    assert payload['available_read_passages'][0]['text'] == text[:12000]
    # Test polluted recent records too, not just records outside the window.
    p.background_agent['observations'].append(deepcopy(p.background_agent['observations'][0]))
    assert 'extracted_text' not in json.dumps(action_payload(p))


@pytest.mark.parametrize('history_size', [15, 350])
@pytest.mark.parametrize('reference', ['0', 'O1'])
def test_old_real_observation_reference_accepted(stage, monkeypatch, history_size, reference):
    s, p = stage
    p.background_agent['observations'] = [
        {'id': str(i), 'action': 'read_source', 'status': 'completed', 'result': {}} for i in range(history_size)]
    authorize(s, p, actions=40)
    payload = action_payload(p)
    assert payload['observation_index'][-1]['observation_alias'] == f'O{history_size}'
    monkeypatch.setattr(s, 'call', lambda *a, **k: action('ask_user', summary='请说明研究范围。', observation_ids=[reference]))
    s.advance(p)
    assert p.background_agent['observations'][-1]['status'] == 'completed'
    assert p.background_agent['observations'][-1]['observation_ids'] == ['0']
    assert p.human_dialogue['pending']['resume_phase'] == 'BACKGROUND_RESEARCH'


def test_repeated_failures_pause_before_budget_exhaustion(stage, monkeypatch):
    s, p = stage
    authorize(s, p, actions=40)
    before = progress_signature(p)
    monkeypatch.setattr(s, 'call', lambda *a, **k: action('read_source', source_id='missing'))
    s.advance(p)
    assert len(p.background_agent['observations']) == 3
    assert p.background_agent['status'] == 'needs_input'
    assert p.phase == ResearchPhase.HUMAN_SOURCE_REVIEW
    p.phase = ResearchPhase.BACKGROUND_RESEARCH
    assert progress_signature(p) == before  # Error artifacts aren't scientific progress.


def test_matching_quote_cannot_bypass_semantic_audit(stage, monkeypatch):
    s, p = stage
    authorize(s, p)
    sid = source(p)
    quote = 'There was no difference compared with sham treatment.'
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', lambda url:
                        {'url': url, 'text': quote, 'sha256': 'hash'})
    s.perform(p, action('read_source', source_id=sid))
    monkeypatch.setattr(s, 'call', lambda *a, **k: SourceAssessmentReview(
        supported=False, rationale='摘要声称优于假对照，与原文矛盾。', issues=[AuditIssue(
            claim_quote='Strong benefit versus sham', source_quote=quote, reason='与原文矛盾')]))
    with pytest.raises(ValueError, match='原文不支持'):
        s.perform(p, action('assess_source', source_id=sid, decision='keep', quote=quote,
                           summary='Strong benefit versus sham', relevance='Relevant', credibility='High certainty'))
    assert not p.background_agent['assessments']


def test_evidence_reasoning_not_forced_into_fast_mode():
    client = StructuredQwenClient()
    token = _call_context.set({'agent_name': 'evidence_researcher', 'output_schema': 'BackgroundAction'})
    try:
        assert client._bounded_options('qwen3.7-plus')['extra_body']['enable_thinking'] is True
        client.request_options = {'extra_body': {'enable_thinking': False}}
        assert client._bounded_options('qwen3.7-plus')['extra_body']['enable_thinking'] is False
    finally:
        _call_context.reset(token)


def test_provider_json_abort_has_one_validated_retry_without_disabling_thinking(monkeypatch):
    from types import SimpleNamespace
    from src.ai_scientist.schemas import StrictModel
    from src.ai_scientist.model_registry import ModelRegistry
    class Output(StrictModel):
        value: str
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            raise RuntimeError('Model output became abnormal while generating JSON for response_format')
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
            'public_response': '格式重试已返回，结果仍须校验。', 'human_input_request': '', 'value': 'valid'})))],
            model='qwen3.8-max', id='response-test', usage=None)
    c = StructuredQwenClient(ModelRegistry({'research_director': 'qwen3.8-max', 'fallback': 'qwen3.8-max'}))
    c.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = c.call('research_director', 'Respond with JSON', {}, Output)
    assert result.value.value == 'valid'
    assert len(requests) == 2 and 'response_format' not in requests[1]
    assert requests[1]['extra_body']['enable_thinking'] is True
    assert result.metadata.attempted_calls == 2 and result.metadata.failed_calls == 1
    assert result.metadata.successful_calls == 1


def test_unified_legacy_clarifications_do_not_disappear(stage):
    s, p = stage
    s.o._queue_clarifications(p, ['请确认范围。'])
    p.human_actions_required = []
    assert p.human_dialogue['pending']['prompt'] == '请确认范围。'
    p.human_dialogue = {'responses': [{'prompt': '请确认范围。', 'response': '有限综述'}]}
    s.o._queue_clarifications(p, ['请确认范围。'])
    assert not p.human_dialogue.get('pending')


@pytest.mark.parametrize('repair_valid', [True, False])
@pytest.mark.parametrize('request_budget', [2, 3])
def test_provider_abort_still_allows_one_schema_repair(repair_valid, request_budget):
    from types import SimpleNamespace
    from src.ai_scientist.schemas import StrictModel
    from src.ai_scientist.model_registry import ModelRegistry
    from src.ai_scientist.exceptions import StructuredOutputError
    class Output(StrictModel):
        value: str
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            raise RuntimeError('Model output became abnormal while generating JSON for response_format')
        content = 'invalid JSON'
        if len(requests) == 3 and repair_valid:
            content = json.dumps({'public_response': '已修复格式。', 'human_input_request': '', 'value': 'valid'})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                               model='qwen3.8-max', id='repair-test', usage=None)
    c = StructuredQwenClient(ModelRegistry({'research_director': 'qwen3.8-max', 'fallback': 'qwen3.8-max'}))
    c.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    c.request_budget = request_budget
    if request_budget == 2:
        with pytest.raises(StructuredOutputError, match='budget exhausted'):
            c.call('research_director', 'Use real evidence', {'source': 'original'}, Output)
    elif repair_valid:
        result = c.call('research_director', 'Use real evidence', {'source': 'original'}, Output)
        assert result.value.value == 'valid'
        assert result.metadata.attempted_calls == 3
    else:
        with pytest.raises(StructuredOutputError, match='after one repair'):
            c.call('research_director', 'Use real evidence', {'source': 'original'}, Output)
    assert len(requests) == len(c.provider_responses) == request_budget
    assert 'original' in json.dumps(requests[-1]['messages'])


def test_schema_repair_keeps_evidence_and_separates_serialization_from_reasoning():
    from types import SimpleNamespace
    from src.ai_scientist.schemas import StrictModel
    from src.ai_scientist.model_registry import ModelRegistry
    class Output(StrictModel):
        value: str
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        body = {'public_response': '结果待核对。', 'human_input_request': '', 'value': 'valid'}
        if len(requests) == 1:
            body['unexpected'] = 'not evidence'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)))],
            model='qwen3.8-max', id='schema-repair-test', usage=None)
    c = StructuredQwenClient(ModelRegistry({'research_director': 'qwen3.8-max', 'fallback': 'qwen3.8-max'}))
    c.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = c.call('research_director', 'Use actual evidence', {'original_source': 'real passage'}, Output)
    assert result.value.value == 'valid' and len(requests) == 2
    assert requests[1]['response_format'] == {'type': 'json_object'}
    assert 'real passage' in json.dumps(requests[1]['messages'])
    assert requests[0]['extra_body']['enable_thinking'] is True
    assert requests[1]['extra_body']['enable_thinking'] is False
    assert 'thinking_budget' not in requests[1]['extra_body']


def test_literature_execution_without_python_with_audit(stage, monkeypatch):
    s, p = stage
    p.research_mode = ResearchMode.SYSTEMATIC_REVIEW
    p.planning_only = False
    p.approval_status = 'valid'
    p.approval_valid_for_versions = s.o._review_artifact_versions(p)
    quote = 'There was no difference compared with sham treatment.'
    sid = source(p)
    p.background_agent['sources'][0]['shown_excerpts'] = [quote]
    e = EvidenceItem(title='Trial review', source_type='web_source', source_url='https://example.org/paper',
                     summary='No difference', source_quote=quote)
    p.evidence = [e]
    def model(self, project, schema, *args, **kwargs):
        if schema == EvidenceRow:
            project.literature_review['last_error'] = {'rationale': 'obsolete row-review opinion 999'}
            return EvidenceRow(evidence_id=e.evidence_id, question='Treatment effect?', study_type='review',
                population_or_system='adults', intervention_or_exposure='treatment', comparator='sham',
                outcome='reported outcome', finding='no demonstrated difference', certainty='uncertain',
                limitations=['Limited sources'], quote=quote)
        if schema == SourceAssessmentReview:
            return SourceAssessmentReview(supported=True, rationale='Faithful to supplied evidence')
        if schema == MarkdownDraft:
            assert args[0]['previous_review'] is None
            return MarkdownDraft(text='# 有限证据综述\n未发现优于假对照的证据。')
        return LiteratureReadiness(decision='complete', limitations=['检索有限'], open_questions=['独立重复'])
    monkeypatch.setattr(BackgroundAgent, 'call', model)
    run_review(s.o, p)
    assert p.literature_review['status'] == 'complete'
    assert len(p.literature_review['rows']) == 1
    assert not p.computation and not p.internal_execution_summary
    assert (s.o.store.project_dir(p.project_id) / 'artifacts/literature_review.md').exists()
    assert run_review(s.o, p) == []  # Resume is idempotent.


def test_literature_requires_real_approval(stage):
    s, p = stage
    with pytest.raises(ValueError, match='审批'):
        run_review(s.o, p)


def test_row_repair_does_not_consume_report_repair_budget(stage, monkeypatch):
    s, p = stage
    p.evidence = [EvidenceItem(title='Real source', source_type='web_source', summary='Bounded evidence')]
    scopes = iter([p.evidence[0].evidence_id, 'report', None])
    def once(*args):
        scope = next(scopes)
        if scope is not None:
            p.literature_review.update(status='needs_revision', revision_scope=scope)
            raise ValueError('Needs a grounded correction')
        return ['completed']
    monkeypatch.setattr('src.ai_scientist.literature_execution._run_review_once', once)
    assert run_review(s.o, p) == ['completed']
    assert p.literature_review['automatic_revisions'] == 2


def test_same_report_repair_remains_bounded(stage, monkeypatch):
    s, p = stage
    calls = []
    def once(*args):
        calls.append(1)
        p.literature_review.update(status='needs_revision', revision_scope='report')
        raise ValueError('Still unsupported')
    monkeypatch.setattr('src.ai_scientist.literature_execution._run_review_once', once)
    with pytest.raises(ValueError, match='Still unsupported'):
        run_review(s.o, p)
    assert len(calls) == 2


def test_report_audit_uses_original_text_not_prior_auditor_claims(stage, monkeypatch):
    s, p = stage
    source(p)
    original = 'The certainty of evidence was generally low or very low.'
    p.background_agent['sources'][0]['shown_excerpts'] = [original]
    p.literature_review = {'rows': [{'evidence_id': 'e1', 'title': 'Trial review',
        'url': 'https://example.org/paper', 'quote': original,
        'finding': 'A bounded finding', 'audit': {'supported': True, 'rationale': 'Unverified extra number 999'}}]}
    assert 'audit' not in synthesis_rows(p)[0]
    payloads = []
    def reviewer(project, schema, payload, **kwargs):
        payloads.append(payload)
        return SourceAssessmentReview(supported=False, rationale='报告错误地将全部结局的确定性合并。',
            issues=[AuditIssue(claim_quote='所有证据均极低。', source_quote=original, reason='确定性不能合并')])
    monkeypatch.setattr(s, 'call', reviewer)
    result = audit_synthesis(s, p, {'report_markdown': '所有证据均极低。'})
    assert not result.supported
    assert payloads[0]['actual_read_passages'] == [{'passage_id': 'P1', 'text': original}]
    assert '999' not in json.dumps(payloads[0])
    assert set(payloads[0]['target_source']) == {'evidence_id', 'title', 'url'}


@pytest.mark.parametrize('protocol_failure', [False, True])
def test_rejected_report_resumes_without_reextracting_successful_rows(stage, monkeypatch, protocol_failure):
    s, p = stage
    p.approval_status = 'valid'
    p.approval_valid_for_versions = s.o._review_artifact_versions(p)
    source(p)
    quote = 'The certainty of evidence was generally low or very low.'
    p.background_agent['sources'][0]['shown_excerpts'] = [quote]
    evidence = EvidenceItem(title='Review', source_type='web_source',
        source_url='https://example.org/paper', summary='Limited findings', source_quote=quote)
    p.evidence = [evidence]
    extraction_count = 0
    report_count = 0
    resuming = False
    def model(self, project, schema, payload, **kwargs):
        nonlocal extraction_count, report_count
        if schema == EvidenceRow:
            extraction_count += 1
            return EvidenceRow(evidence_id=evidence.evidence_id, question='effect?', study_type='review',
                population_or_system='adults', intervention_or_exposure='treatment', comparator='control',
                outcome='outcome', finding='Uncertain effects', certainty='Low to very low',
                limitations=['Limited sources'], quote=quote)
        if schema == LiteratureReadiness:
            return LiteratureReadiness(decision='complete', limitations=['Limited sources'], open_questions=[])
        if schema == MarkdownDraft:
            report_count += 1
            assert 'audit' not in payload['evidence_rows'][0]
            assert payload['source_read_receipts'][0]['actual_read_passages'] == [quote]
            if report_count > 1:
                assert payload['previous_review']['supported'] is False
            return MarkdownDraft(text='Bounded review')
        if payload.get('target_source') and not resuming:
            return SourceAssessmentReview(supported=False, rationale='Correct certainty levels',
                issues=[AuditIssue(claim_quote='Different draft' if protocol_failure else 'Bounded review',
                    source_quote=quote, reason='Correct certainty levels')])
        return SourceAssessmentReview(supported=True, rationale='Matches original')
    monkeypatch.setattr(BackgroundAgent, 'call', model)
    with pytest.raises(ValueError, match='原文校验' if protocol_failure else '未发布'):
        run_review(s.o, p, max_revisions=0)
    assert p.literature_review['status'] == ('auditing' if protocol_failure else 'needs_revision')
    assert not (s.o.store.project_dir(p.project_id) / 'artifacts/literature_review.md').exists()
    resuming = True
    run_review(s.o, p)
    assert extraction_count == 1 and report_count == (1 if protocol_failure else 2)
    assert p.literature_review['status'] == 'complete'


def test_user_title_survives_model_revision(stage):
    from src.ai_scientist.schemas import DirectorOutput, ResearchQuestion
    s, p = stage
    p.title = '用户指定名称'
    p.title_user_edited = True
    s.o._apply_revision_output(p, 'question', DirectorOutput(project_title='模型建议名称',
        research_question=ResearchQuestion(original_question='问题', normalized_question='问题')))
    assert p.title == '用户指定名称'


def test_literature_stale_approval_rejected(stage):
    s, p = stage
    p.approval_status = 'valid'
    p.approval_valid_for_versions = s.o._review_artifact_versions(p)
    p.active_artifact_versions['study_design'] = 2
    with pytest.raises(ValueError, match='重新审批'):
        run_review(s.o, p)


def test_action_messages_are_not_labeled_research_complete():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
from src.ai_scientist.conversation_ui import render_research_conversation
project = {'project_id': 'p', 'background_agent': {'observations': [
    {'call_id': 'c', 'status': 'failed', 'result': {'error': '原文不匹配'}}]}}
render_research_conversation(project, [{'call_id': 'c', 'message_kind': 'action_proposal',
    'status': 'completed', 'public_response': '计划评估来源'}], {})
''').run()
    assert not app.exception
    assert any('行动提议' in e.label for e in app.expander)
    assert any('实际行动失败' in e.value for e in app.error)
    assert not any('已完成' in e.label for e in app.expander)


def test_budget_renewal_does_not_require_network_reapproval(stage):
    from streamlit.testing.v1 import AppTest
    s, p = stage
    authorize(s, p)
    p.phase = ResearchPhase.SEARCH_PLAN_REVIEW
    p.background_agent['status'] = 'budget_paused'
    app = AppTest.from_string('''
import streamlit as st
from src.ai_scientist.background_agent_ui import render_background_agent
render_background_agent(st.session_state.project, False, lambda *a: None)
''')
    app.session_state.project = p.model_dump(mode='json')
    app.run()
    assert not app.exception
    assert any(b.label == '追加额度并继续研究' for b in app.button)
    assert not any('允许本阶段访问' in c.label for c in app.checkbox)


def test_legacy_adoption_has_explicit_reaudit_action(stage):
    from streamlit.testing.v1 import AppTest
    s, p = stage
    authorize(s, p)
    sid = source(p)
    p.phase = ResearchPhase.HUMAN_SOURCE_REVIEW
    p.background_agent.update(status='awaiting_adoption', proposal_id='legacy-proposal',
        assessments={sid: {'decision': 'keep', 'quote_matched': True}})
    app = AppTest.from_string('''
import streamlit as st
from src.ai_scientist.background_agent_ui import render_background_agent
def action(url, payload):
    st.session_state.submission = (url, payload)
render_background_agent(st.session_state.project, False, action)
''')
    app.session_state.project = p.model_dump(mode='json')
    app.run()
    assert not app.exception
    button = next(b for b in app.button if b.label == '重新核查旧版资料建议')
    button.click().run()
    assert app.session_state.submission[0].endswith('/feedback')
    assert app.session_state.submission[1]['regenerate'] is False
    assert next(b for b in app.button if b.label == '确认研究依据并继续研究').disabled


def test_auditor_cannot_introduce_unsupported_numeric_correction():
    from src.ai_scientist.grounded_audit import audit_errors
    text = 'The interval was 0.66 to 7.33 in this study.'
    review = SourceAssessmentReview(supported=False, rationale='Incorrect correction', issues=[AuditIssue(
        claim_quote='0.66 to 7.33', source_quote=text, reason='Change upper limit', suggested_text='0.66 to 3.33')])
    assert any('numbers' in e for e in audit_errors(review, {'text': text}, [text]))
    review.issues[0].source_quote = 'The interval was 0.66 to 3.33 in this study.'
    assert any('source_quote' in e for e in audit_errors(review, {'text': text}, [text]))


def test_adoption_view_does_not_inherit_show_all_discovery_default(stage):
    from streamlit.testing.v1 import AppTest
    _, p = stage
    source(p)
    p.background_agent['sources'][0]['read_ranges'] = [{'start': 0, 'end': 50}]
    source(p)
    p.phase = ResearchPhase.HUMAN_SOURCE_REVIEW
    app = AppTest.from_string('''
import streamlit as st
from src.ai_scientist.background_agent_ui import render_background_agent
render_background_agent(st.session_state.project, False, lambda *a: None)
''')
    app.session_state.project = p.model_dump(mode='json')
    app.session_state[f'basis_unread_{p.project_id}_False'] = True
    app.run()
    assert not app.exception
    unread = next(c for c in app.checkbox if c.label.startswith('显示尚未阅读'))
    assert unread.value is False


def test_later_roles_receive_original_reading_not_only_summaries(stage):
    from src.ai_scientist.research_context import adopted_source_passages
    _, p = stage
    for n in range(2):
        p.evidence.append(EvidenceItem(title=f'Paper {n}', summary='Fallible summary',
            source_url=f'https://example.org/{n}', source_type='paper', reliability='medium',
            relevance='Relevant to the question'))
        p.background_agent['sources'].append({'url': f'https://example.org/{n}',
            'retrieval_snapshot': {'retrieved_at': '2026-10-11T00:00:00Z', 'sha256': 'recorded-hash'},
            'shown_excerpts': [f'Actual methods {n}. ' + 'x' * 15000, 'Second actual read page.']})
    p.background_agent['observations'] = [{'id': 'search', 'action': 'search_web', 'status': 'completed',
        'started_at': '2026-10-11T00:00:00Z', 'result': {'query': 'actual search', 'candidates': ['large list']}}]
    context = adopted_source_passages(p, max_chars=24000)
    assert sum(len(t) for e in context['sources'] for t in e['actual_read_passages']) == 24000
    assert all(e['actual_read_passages'][0].startswith('Actual methods') for e in context['sources'])
    assert all(e['omitted_read_chars'] > 0 for e in context['sources'])
    assert 'Fallible summary' not in json.dumps(context)
    assert context['sources'][0]['retrieval_snapshot']['sha256'] == 'recorded-hash'
    assert context['search_log'][0]['started_at'] == '2026-10-11T00:00:00Z'
    assert 'candidates' not in context['search_log'][0]['result']


def test_plan_revision_form_submits_and_continues():
    import ast
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    module = ast.parse(Path('app_streamlit.py').read_text())
    function = ast.unparse(next(n for n in module.body if isinstance(n, ast.FunctionDef)
                               and n.name == '_render_plan_approval'))
    app = AppTest.from_string('''
import streamlit as st
BackendAPIError = RuntimeError
def _render_research_asset_uploader(*a, **k): pass
def _render_mapping_sections(*a, **k): pass
def get_json(*a): return {'ready_for_approval': False}
def _research_action(*a, **k): st.session_state.submission = (a, k)
''' + function + "\n_render_plan_approval('', {'project_id': 'test', 'phase': 'HUMAN_APPROVAL'}, False, False)")
    app.run()
    next(b for b in app.button if b.label == '提交意见并开始修订').click().run()
    assert any('尚未提交' in w.value for w in app.warning)
    app.selectbox[0].set_value('design')
    app.text_area[0].set_value('请核对实际研究地点。')
    next(b for b in app.button if b.label == '提交意见并开始修订').click().run()
    assert not app.exception
    args, kwargs = app.session_state.submission
    assert args[2] == {'target': 'design', 'feedback': '请核对实际研究地点。'}
    assert kwargs['continue_after'] is True


def test_saved_revision_feedback_is_visible_without_claiming_completed():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
from src.ai_scientist.conversation_ui import render_research_conversation
render_research_conversation({'project_id': 'test', 'revision_feedback': ['请核查地点。']}, [], {})
''').run()
    assert not app.exception
    assert any(e.label == '你最近的修订意见 · 已保存' for e in app.expander)
    assert any('已保存不等于已落实' in c.value for c in app.caption)


def test_only_grounded_issues_are_reused_as_corrections():
    from types import SimpleNamespace
    from src.ai_scientist.grounded_audit import grounded_review
    review = SourceAssessmentReview(supported=False, rationale='Replace the result with 999.',
        issues=[AuditIssue(claim_quote='proved no effect',
            source_quote='The estimate was imprecise.',
            reason='Imprecision does not establish equivalence.',
            suggested_text='The estimate was imprecise.')])
    result = grounded_review(SimpleNamespace(call=lambda *a, **k: review), None, {
        'proposed_assessment': {'text': 'This proved no effect'},
        'actual_read_passages': ['The estimate was imprecise.']})
    assert '999' not in result.model_dump_json()
    assert result.rationale == 'Imprecision does not establish equivalence.'


def test_one_invalid_suggestion_does_not_discard_valid_rejection():
    from types import SimpleNamespace
    from src.ai_scientist.grounded_audit import grounded_review
    review = SourceAssessmentReview(supported=False, rationale='Mixed audit', issues=[
        AuditIssue(claim_quote='certain effect', source_quote='The estimate was imprecise.',
                   reason='Do not claim certainty.'),
        AuditIssue(claim_quote='certain effect', source_quote='The estimate was imprecise.',
                   reason='The sample size was 999.')])
    calls = []
    def call(*a, **k):
        calls.append(1)
        return review
    result = grounded_review(SimpleNamespace(call=call), None, {
        'proposed_assessment': {'text': 'certain effect'}, 'actual_read_passages': ['The estimate was imprecise.']})
    assert result.supported is False and len(result.issues) == len(calls) == 1
    assert '999' not in result.model_dump_json()


def test_audit_can_cite_multiple_original_passages():
    from src.ai_scientist.grounded_audit import audit_errors
    primary = 'The comparison gave P = 0.060.'
    additional = 'The distance effect gave P = 0.046.'
    review = SourceAssessmentReview(supported=False, rationale='Qualify the claim', issues=[AuditIssue(
        claim_quote='proved avoidance', source_quote=primary, supporting_quotes=[additional],
        reason='Distinguish P = 0.060 from the distance effect P = 0.046.')])
    assert not audit_errors(review, {'text': 'This proved avoidance'}, [primary, additional])
    assert audit_errors(review, {'text': 'This proved avoidance'}, [primary])


def test_audit_correction_can_use_read_context_but_not_invented_values():
    from src.ai_scientist.grounded_audit import audit_errors
    primary = 'The comparison gave P = 0.058.'
    other = 'The plot displays significance levels P <= 0.1.'
    review = SourceAssessmentReview(supported=False, rationale='Qualify', issues=[AuditIssue(
        claim_quote='proved an effect', source_quote=primary,
        reason='The plot also displays P <= 0.1.', suggested_text='The comparison gave P = 0.058.')])
    assert not audit_errors(review, {'text': 'proved an effect'}, [primary, other])
    review.issues[0].suggested_text = 'The comparison gave P = 0.1.'
    # Presence is not scientific validation of this attribution: that remains
    # the semantic audit's job. It must not fail merely for quote placement.
    assert not audit_errors(review, {'text': 'proved an effect'}, [primary, other])
    review.issues[0].suggested_text = 'The comparison gave P = 3.33.'
    assert audit_errors(review, {'text': 'proved an effect'}, [primary, other])
    review.issues[0].suggested_text = ''
    review.issues[0].reason = 'Use 3.33 instead.'
    assert audit_errors(review, {'text': 'proved an effect'}, [primary, other])


def test_audit_preserves_spaced_mathematical_minus():
    from src.ai_scientist.grounded_audit import numbers
    assert numbers('estimate = − 0.24') == numbers('estimate = -0.24')
    assert numbers('estimate = − 0.24') != numbers('estimate = 0.24')


def test_plain_markdown_preserves_quotes_without_json_envelope():
    from types import SimpleNamespace
    from src.ai_scientist.model_registry import ModelRegistry
    requests = []
    document = '# Evidence\n\n"Quoted" text and a | table | cell.\n'
    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=document))],
                               id='markdown-test', model='qwen3.8-max', usage=None)
    client = StructuredQwenClient(ModelRegistry({'scientific_synthesizer': 'qwen3.8-max'}))
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = client.call('scientific_synthesizer', 'Write a report.', {'source': 'real'}, MarkdownDraft)
    assert result.value.text == document == result.metadata.public_response
    assert 'response_format' not in requests[0]
    assert result.metadata.attempted_calls == 1


def test_web_reader_preserves_table_rows(monkeypatch):
    from src.ai_scientist.tools.research_web import read_source_snapshot
    html = '<p>Original research describing evidence and uncertainty in detail.</p><table><tr><th>Outcome</th><th>Upper limit</th></tr><tr><td>Nausea</td><td>7.33</td></tr></table>'
    monkeypatch.setattr('src.ai_scientist.tools.research_web.fetch_public',
                        lambda url: (url, 'text/html', html.encode()))
    text = read_source_snapshot('https://example.org')['text']
    assert '\n| Outcome | Upper limit\n| Nausea | 7.33' in text


def test_complete_json_fence_is_unwrapped_but_prose_is_not_guessed():
    from src.ai_scientist.structured_client import json_document
    assert json_document('```json\n{"value":"real"}\n```') == '{"value":"real"}'
    prose = 'Perhaps use {"value":"guessed"}'
    assert json_document(prose) == prose
    with pytest.raises(json.JSONDecodeError):
        json.loads(json_document(prose))


def test_tool_capability_alias_does_not_require_paid_repair():
    assert action('web_search', query='pollination light').action == 'search_web'
    with pytest.raises(ValueError):
        action('arbitrary_command')


def test_failed_reply_does_not_look_like_still_generating():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
from src.ai_scientist.conversation_ui import render_research_conversation
render_research_conversation({'project_id':'p'}, [{'status':'failure','public_response':'Unfinished draft'}], {})
''').run()
    assert not app.exception
    assert any('调用未完成' in x.label for x in app.expander)
    assert not any('正在生成' in x.label for x in app.expander)


def test_crossref_query_documents_actual_scope_and_excludes_components(monkeypatch):
    from src.ai_scientist.tools.research_web import search_papers
    from urllib.parse import parse_qs, urlsplit
    seen = []
    def fetch(url):
        seen.append(url)
        return url, 'application/json', b'{"message":{"items":[]}}'
    monkeypatch.setattr('src.ai_scientist.tools.research_web.fetch_public', fetch)
    result = search_papers('"night light" AND (pollination)')
    query = parse_qs(urlsplit(seen[0]).query)
    assert query['filter'] == ['type:journal-article']
    assert query['query.title'] == ['night light pollination']
    assert 'not full text' in result['search_scope']


def test_markdown_stream_is_direct_public_text_and_ignores_private_reasoning():
    from types import SimpleNamespace as NS
    from src.ai_scientist.model_progress import model_progress
    from src.ai_scientist.model_registry import ModelRegistry
    chunks = [NS(model='qwen3.8-max', id='stream-test', usage=None,
        choices=[NS(finish_reason=None, delta=NS(content=s, reasoning_content='PRIVATE'))])
        for s in ['# Report\n', 'Actual evidence.']]
    c = StructuredQwenClient(ModelRegistry({'scientific_synthesizer': 'qwen3.8-max'}))
    c.client = NS(chat=NS(completions=NS(create=lambda **kwargs: iter(chunks))))
    events = []
    with model_progress(events.append):
        result = c.call('scientific_synthesizer', 'Write.', {}, MarkdownDraft)
    assert result.metadata.public_response == '# Report\nActual evidence.'
    assert any(e.get('public_response') == result.value.text for e in events)
    assert 'PRIVATE' not in json.dumps(events)
