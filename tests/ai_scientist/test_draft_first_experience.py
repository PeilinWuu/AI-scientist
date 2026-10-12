"""UX behavior and provenance boundaries, without making scientific-quality claims."""
import json
from types import SimpleNamespace as NS

import pytest
from pydantic import BaseModel
from src.ai_scientist.background_agent import BackgroundAgent, SourceAssessmentReview, AuditIssue
from src.ai_scientist.literature_experience import run_draft, review_draft, finish_draft, export_report, source_context
from src.ai_scientist.structured_client import StructuredQwenClient, MarkdownDraft, narrative_text
from src.ai_scientist.model_registry import ModelRegistry
from src.ai_scientist.model_progress import model_progress
from src.ai_scientist.schemas import EvidenceItem, ResearchMode, ResearchPhase
from src.ai_scientist.workflow_ui_state import next_research_action
from src.ai_scientist.continuous_research import pause_reason
from src.ai_scientist.grounded_audit import quote_text
from tests.ai_scientist.test_background_agent import stage, source
from tests.test_conversation_api import api


def setup_project(o, p):
    p.research_mode = ResearchMode.SYSTEMATIC_REVIEW
    p.phase = ResearchPhase.EXECUTION_WAITING
    p.planning_only = False
    p.approval_status = 'valid'
    p.approval_valid_for_versions = o._review_artifact_versions(p)
    p.background_agent.setdefault('sources', [])
    source(p)
    text = 'There was no difference compared with sham treatment.'
    p.background_agent['sources'][0]['shown_excerpts'] = [text, text]
    p.evidence = [EvidenceItem(title='Trial', source_type='web_source',
        source_url='https://example.org/paper', summary='Limited evidence', source_quote=text)]
    o.store.save(p)
    return text


def test_one_call_draft_and_no_silent_review(stage, monkeypatch):
    s, p = stage
    text = setup_project(s.o, p)
    calls = []
    def model(self, project, schema, payload, **kw):
        calls.append(schema)
        assert schema is MarkdownDraft
        assert payload['sources'][0]['actual_read_passages'] == [text]
        return MarkdownDraft(text='# 回答\n研究证据有限。')
    monkeypatch.setattr(BackgroundAgent, 'call', model)
    run_draft(s.o, p)
    assert calls == [MarkdownDraft]
    assert p.literature_review['status'] == 'draft_ready'
    assert pause_reason(p) == 'literature_draft_ready'
    assert next_research_action(p.model_dump(mode='json'))['kind'] == 'literature_draft'
    assert run_draft(s.o, p) == []
    assert len(calls) == 1
    assert '未进行补充核查' in export_report(p.literature_review)
    with pytest.raises(ValueError, match='确认'):
        finish_draft(s.o, p)


def test_review_protocol_failure_keeps_answer_and_is_not_approval(stage, monkeypatch):
    s, p = stage
    setup_project(s.o, p)
    p.literature_review = {'status': 'draft_ready', 'draft': {'report_markdown': '有限回答'}}
    calls = []
    def review(*args, **kwargs):
        calls.append(1)
        return SourceAssessmentReview(supported=False, rationale='unlocated', issues=[
            AuditIssue(claim_quote='有限回答', source_quote='Invented source sentence', reason='check')])
    monkeypatch.setattr(BackgroundAgent, 'call', review)
    review_draft(s.o, p)
    assert calls == [1]
    assert p.literature_review['review_status'] == 'unavailable'
    assert p.literature_review['draft']['report_markdown'] == '有限回答'
    assert p.literature_review['status'] == 'draft_ready'
    assert '补充核查未完成' in export_report(p.literature_review)


def test_review_cache_is_invalidated_by_changed_draft(stage, monkeypatch):
    s, p = stage
    setup_project(s.o, p)
    p.literature_review = {'draft': {'report_markdown': 'Original answer'}}
    calls = []
    def model(*args, **kwargs):
        calls.append(1)
        return SourceAssessmentReview(supported=True, rationale='No material problem identified')
    monkeypatch.setattr(BackgroundAgent, 'call', model)
    review_draft(s.o, p)
    review_draft(s.o, p)
    assert len(calls) == 1
    p.literature_review['draft']['report_markdown'] = 'Changed answer'
    review_draft(s.o, p)
    assert len(calls) == 2


def test_review_network_failure_clears_running_state(stage, monkeypatch):
    s, p = stage
    setup_project(s.o, p)
    p.literature_review = {'draft': {'report_markdown': 'Original answer'}}
    def failed(*args, **kwargs):
        raise TimeoutError('network')
    monkeypatch.setattr(BackgroundAgent, 'call', failed)
    with pytest.raises(TimeoutError):
        review_draft(s.o, p)
    assert p.literature_review['status'] == 'draft_ready'
    assert p.literature_review['review_status'] == 'unavailable'


def test_explicit_finish_completes_without_model_rewrite(api, monkeypatch):
    p = api.project
    setup_project(api.o, p)
    p.literature_review = {'status': 'draft_ready', 'draft': {'report_markdown': '# Original answer'},
                           'review_status': 'issues_found', 'review_notes': ['A claim needs checking.']}
    api.o.store.save(p)
    def no_model(*args, **kwargs):
        raise AssertionError('Finishing must not call a model')
    monkeypatch.setattr(BackgroundAgent, 'call', no_model)
    monkeypatch.setattr(api.o, '_run_agent', no_model)
    url = f'/api/research/{p.project_id}/literature/action'
    assert api.client.post(url, json={'action': 'finish'}).status_code == 422
    response = api.client.post(url, json={'action': 'finish', 'acknowledged': True})
    assert response.status_code == 202
    assert api.client.post(url, json={'action': 'review'}).status_code == 409
    api.threads[-1].run()
    saved = api.o.get_project(p.project_id)
    assert saved.phase == ResearchPhase.COMPLETED
    assert saved.literature_review['review_status'] == 'issues_found'
    assert saved.literature_review['report_markdown'] == '# Original answer'
    artifact = (api.o.store.project_dir(p.project_id) / 'artifacts/literature_review.md').read_text()
    assert 'A claim needs checking.' in artifact


def test_stale_approval_cannot_be_bypassed_by_draft_actions(api):
    p = api.project
    setup_project(api.o, p)
    p.literature_review = {'draft': {'report_markdown': 'answer'}}
    p.approval_status = 'stale'
    api.o.store.save(p)
    assert api.client.post(f'/api/research/{p.project_id}/literature/action',
                          json={'action': 'review'}).status_code >= 400
    assert not api.threads


def test_typographic_quote_normalization_keeps_scientific_signs():
    assert quote_text('The authors’ ﬁndings') == quote_text("The authors' findings")
    assert quote_text('CI −0.5') != quote_text('CI 0.5')


def test_narrative_stream_keeps_control_out_of_public_answer():
    class Output(BaseModel):
        value: str
    chunks = ['# 研究发现\n资料支持有限结论。', '\n<research-', 'control>\n',
              '{"value":"saved","human_input_request":""}']
    calls, events = [], []
    def create(**kwargs):
        calls.append(kwargs)
        return iter([NS(model='qwen3.8-max', id='test', usage=None,
            choices=[NS(finish_reason=None, delta=NS(content=t, reasoning_content='PRIVATE'))]) for t in chunks])
    c = StructuredQwenClient(ModelRegistry({'research_director': 'qwen3.8-max'}))
    c.client = NS(chat=NS(completions=NS(create=create)))
    c.narrative_first = True
    with model_progress(events.append):
        result = c.call('research_director', 'Investigate.', {}, Output)
    assert len(calls) == 1 and 'response_format' not in calls[0]
    assert result.value.value == 'saved'
    assert result.metadata.public_response == chunks[0]
    assert all('<research-' not in e.get('public_response', '') for e in events)
    assert 'PRIVATE' not in json.dumps(events)
    receipt = result.metadata.provider_responses[0]
    assert receipt['first_chunk_seconds'] >= 0
    assert receipt['first_content_seconds'] >= 0
    assert receipt['total_seconds'] >= receipt['first_content_seconds']


def test_partial_control_marker_never_visible():
    marker = '<research-control>'
    for i in range(1, len(marker)):
        assert narrative_text('Answer\n' + marker[:i]) == 'Answer'


def test_readable_draft_visible_after_failed_job():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
from src.ai_scientist.literature_ui import render_literature_answer
render_literature_answer({'literature_review': {'status':'auditing', 'draft':
    {'report_markdown':'# 有用的研究回答'}, 'last_error': {'rationale':'某个结论待核对'}}})
''').run()
    assert not app.exception
    assert any('有用的研究回答' in m.value for m in app.markdown)
    assert any('最终核查未完成' in c.value for c in app.caption)


def test_empty_revision_does_not_submit():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string('''
import streamlit as st
from src.ai_scientist.literature_ui import render_draft_actions
render_draft_actions({'project_id':'p'}, lambda p: st.error('UNEXPECTED_SUBMIT'))
''').run()
    next(b for b in app.button if b.label == '提交意见并修改回答').click().run()
    assert not app.error and any('填写修改意见' in w.value for w in app.warning)
