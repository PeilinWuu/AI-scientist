"""User-path regressions; model doubles are not a scientific validation."""
from types import SimpleNamespace as NS

import pytest
from streamlit.testing.v1 import AppTest

from src.ai_scientist.background_agent import BackgroundAgent, SourceAssessmentReview, AuditIssue
from src.ai_scientist.grounded_audit import grounded_review, AuditProtocolError
from src.ai_scientist.literature_experience import run_draft
from src.ai_scientist.structured_client import MarkdownDraft
from src.ai_scientist.schemas import ResearchPhase, StudyDesign, AnalysisPlan
from src.ai_scientist.experience_ui import approval_summary
from tests.ai_scientist.test_background_agent import stage
from tests.ai_scientist.test_draft_first_experience import setup_project
from tests.test_conversation_api import api


def test_approved_plan_reaches_writer_and_revision_keeps_audit_history(stage, monkeypatch):
    s, p = stage
    setup_project(s.o, p)
    p.study_design = StudyDesign(research_mode=p.research_mode, objective='Bounded synthesis',
                                risks=['Do not extrapolate species'])
    p.analysis_plan = AnalysisPlan(success_criteria=['Link original sources'])
    p.literature_review = {'draft': {'report_markdown': 'Old answer'}, 'revision_request': 'Shorter',
                           'review_status': 'issues_found', 'audit': {'rationale': 'Check species'},
                           'review_notes': ['Check species']}
    def call(self, project, schema, payload, **kwargs):
        brief = payload['approved_writing_brief']
        assert brief['study_design']['risks'] == ['Do not extrapolate species']
        assert brief['analysis_plan']['success_criteria'] == ['Link original sources']
        assert payload['review_advice_not_evidence']['rationale'] == 'Check species'
        return MarkdownDraft(text='New answer')
    monkeypatch.setattr(BackgroundAgent, 'call', call)
    run_draft(s.o, p)
    assert p.literature_review['draft_history'][0]['review_status'] == 'issues_found'
    assert p.literature_review['review_status'] == 'not_requested'
    assert 'audit' not in p.literature_review


def test_passage_id_copies_exact_original_without_paid_repair():
    text = 'Only the nucleation step occurs at this temperature.'
    def call(project, schema, payload, **kwargs):
        assert payload['actual_read_passages'][0] == {'passage_id': 'P1', 'text': text}
        return SourceAssessmentReview(supported=False, rationale='Check', issues=[
            AuditIssue(claim_quote='Nothing occurs', source_passage_id='P1', reason='The source differs')])
    result = grounded_review(NS(call=call), None,
        {'proposed_assessment': 'Nothing occurs', 'actual_read_passages': [text]}, max_attempts=1)
    assert result.issues[0].source_quote == text
    assert not result.supported


@pytest.mark.parametrize('locator,supporting', [('P404', []), ('P1', ['P404'])])
def test_invented_locator_cannot_certify_review(locator, supporting):
    def call(*args, **kwargs):
        return SourceAssessmentReview(supported=False, rationale='Check', issues=[AuditIssue(
            claim_quote='Claim', source_passage_id=locator, supporting_passage_ids=supporting,
            source_quote='A valid passage cannot rescue an invented locator.', reason='Check')])
    with pytest.raises(AuditProtocolError):
        grounded_review(NS(call=call), None, {'proposed_assessment': 'Claim',
            'actual_read_passages': ['A valid passage cannot rescue an invented locator.']}, max_attempts=1)


def test_completed_draft_reopens_only_after_explicit_action(api):
    p = api.project
    setup_project(api.o, p)
    p.phase = ResearchPhase.COMPLETED
    p.literature_review = {'status': 'complete', 'delivery_policy': 'draft_first',
        'draft': {'report_markdown': 'Saved answer'}, 'report_markdown': 'Saved answer',
        'review_status': 'unavailable', 'completed_at': 'old timestamp',
        'human_delivery_acknowledged': True}
    api.o.store.save(p)
    url = f'/api/research/{p.project_id}/literature/action'
    assert api.client.post(url, json={'action': 'revise', 'feedback': ''}).status_code == 422
    assert api.o.get_project(p.project_id).phase == ResearchPhase.COMPLETED
    assert api.client.post(url, json={'action': 'revise', 'feedback': 'Clarify limits'}).status_code == 202
    saved = api.o.get_project(p.project_id)
    assert saved.phase == ResearchPhase.EXECUTION_WAITING
    assert saved.approval_status == 'valid'
    assert saved.literature_review['delivery_history'][0]['report_markdown'] == 'Saved answer'
    assert 'human_delivery_acknowledged' not in saved.literature_review


def test_compact_approval_uses_real_schema_fields():
    assert ('本轮要做', 'Read existing sources') in approval_summary({
        'study_design': {'objective': 'Read existing sources', 'risks': ['Limited scope']}})


def test_exhausted_budget_does_not_reopen_completed_delivery(api):
    p = api.project
    setup_project(api.o, p)
    p.phase = ResearchPhase.COMPLETED
    p.budget.attempted_model_calls = p.budget.max_model_calls
    p.literature_review = {'delivery_policy': 'draft_first', 'draft': {'report_markdown': 'Saved'}}
    api.o.store.save(p)
    assert api.client.post(f'/api/research/{p.project_id}/literature/action',
                          json={'action': 'review'}).status_code == 409
    assert api.o.get_project(p.project_id).phase == ResearchPhase.COMPLETED
    assert not api.threads


def test_budget_renewal_is_explicit_and_never_runs_or_reopens(api):
    p = api.project
    setup_project(api.o, p)
    p.phase = ResearchPhase.COMPLETED
    p.literature_review = {'delivery_policy': 'draft_first', 'draft': {'report_markdown': 'Saved'}}
    api.o.store.save(p)
    url = f'/api/research/{p.project_id}/literature/action'
    higher = p.budget.max_model_calls + 10
    assert api.client.post(url, json={'action': 'budget', 'max_model_calls': higher}).status_code == 422
    assert api.client.post(url, json={'action': 'budget', 'max_model_calls': higher,
                                    'acknowledged': True}).status_code == 200
    saved = api.o.get_project(p.project_id)
    assert saved.budget.max_model_calls == higher
    assert saved.phase == ResearchPhase.COMPLETED
    assert not api.threads


def test_review_feedback_is_next_to_one_click_revision():
    app = AppTest.from_string('''
import streamlit as st
from src.ai_scientist.literature_ui import render_literature_answer
render_literature_answer({'project_id':'p', 'phase':'EXECUTION_WAITING', 'literature_review': {
    'status':'draft_ready','draft':{'report_markdown':'Current answer'},
    'review_status':'issues_found','review_notes':['Check source attribution']}},
    submit=lambda payload: st.session_state.update(submitted=payload))
''').run()
    assert not app.exception
    next(b for b in app.button if b.label == '根据核查意见修改回答').click().run()
    assert app.session_state['submitted']['action'] == 'revise'
    assert 'Check source attribution' in app.session_state['submitted']['feedback']


def test_adoption_selection_before_reading_answer_and_no_duplicate_widgets():
    app = AppTest.from_string('''
import streamlit as st
from src.ai_scientist.background_agent_ui import render_background_agent
render_background_agent({'project_id':'p','phase':'HUMAN_SOURCE_REVIEW','background_agent': {
  'scope_id':'scope','proposal_id':'proposal','scope':{'external_information':'required'},
  'status':'awaiting_adoption','partial_answer':{'markdown':'Long reading answer'},
  'sources':[{'candidate_id':'s','title':'Paper - PMC Lock','source_alias':'S1','read_ranges':[{}]}],
  'assessments':{'s':{'decision':'keep','quote_matched':True,'assessment_mode':'reading_note'}}}},
  False, lambda path,payload: st.session_state.update(adopted=payload))
''').run()
    assert not app.exception
    assert len([b for b in app.button if b.label == '确认研究依据并继续研究']) == 1
    box = next(c for c in app.checkbox if c.label == 'Paper - PMC')
    box.check().run()
    next(b for b in app.button if b.label == '确认研究依据并继续研究').click().run()
    assert app.session_state['adopted']['source_ids'] == ['s']
    assert not next(e for e in app.expander if '阅读阶段回答' in e.label).proto.expanded
