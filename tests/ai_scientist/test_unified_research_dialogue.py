"""Unified researcher path. Model doubles test control flow, not scientific truth."""
import pytest
from streamlit.testing.v1 import AppTest
from src import main_api

from src.ai_scientist.background_agent import BackgroundAgent
from src.ai_scientist.research_dialogue import DialogueDecision, allowed_actions
from src.ai_scientist.research_timeline import live_answer, timeline_entries
from src.ai_scientist.schemas import ResearchPhase
from src.ai_scientist.structured_client import MarkdownDraft
from src.ai_scientist.model_progress import emit_model_progress
from src.ai_scientist.exceptions import ModelInputRequired
from src.ai_scientist.workflow_ui_state import next_research_action
from tests.test_conversation_api import api
from tests.ai_scientist.test_draft_first_experience import setup_project


def send(api, text='请解释这项发现', mid='message1', **extra):
    return api.client.post(f'/api/research/{api.project.project_id}/message',
                           json={'message': text, 'message_id': mid, **extra})


def model(api, action='discuss', **fields):
    def call(self, project, schema, payload, **kw):
        if schema is MarkdownDraft:
            self.last_call_id = 'writer1'
            return MarkdownDraft(text='Revised answer with source limits.')
        assert schema is DialogueDecision
        assert 'allowed_actions' in payload
        emit_model_progress({'call_id': 'decision1', 'agent_name': 'scientific_synthesizer',
            'message_kind': 'conversation_answer', 'status': 'completed',
            'public_response': '模型直接回答', 'timestamp': '2026-10-13T10:00:00Z'})
        return DialogueDecision(action=action, public_response='模型直接回答', **fields)
    api.monkeypatch.setattr(BackgroundAgent, 'call', call)


def draft(api):
    p = api.project
    setup_project(api.o, p)
    p.phase = ResearchPhase.COMPLETED
    p.literature_review = {'status': 'complete', 'delivery_policy': 'draft_first',
        'draft': {'report_markdown': 'Original answer'}, 'report_markdown': 'Original answer',
        'review_status': 'checked', 'review_notes': ['Old review']}
    api.o.store.save(p)
    return p


def test_discussion_does_not_advance_workflow_or_approve(api):
    model(api)
    assert send(api).status_code == 202
    api.threads[-1].run()
    p = api.o.get_project(api.project.project_id)
    assert p.phase == ResearchPhase.HYPOTHESIS_GENERATION
    assert p.approval_status != 'valid'
    assert p.human_dialogue['messages'][0]['response'] == '模型直接回答'
    job = api.jobs.load(p.project_id, p.human_dialogue['messages'][0]['job_id'])
    assert job.status == 'completed'
    response = api.client.get(f'/api/research/{p.project_id}/conversation').json()
    assert response['messages'][0]['dialogue_message_id'] == 'message1'
    assert response['user_messages'][0]['text'] == '请解释这项发现'


def test_same_turn_retries_are_idempotent(api):
    model(api)
    send(api)
    assert send(api).status_code == 409  # Running mutation middleware.
    api.threads[0].run()
    assert send(api).status_code == 200
    assert len(api.threads) == 1
    assert send(api, text='different text').status_code == 409


def test_draft_revision_updates_original_and_keeps_prior_review(api):
    p = draft(api)
    model(api, 'revise_answer', instructions='Preserve source limits')
    send(api, text='请缩短这份回答，保留来源限制')
    api.threads[-1].run()
    p = api.o.get_project(p.project_id)
    assert p.literature_review['draft']['report_markdown'] == 'Revised answer with source limits.'
    assert p.literature_review['draft']['call_id'] == 'writer1'
    assert p.literature_review['draft_history'][0]['report_markdown'] == 'Original answer'
    assert p.literature_review['draft_history'][0]['review_status'] == 'checked'
    assert p.literature_review['review_status'] == 'not_requested'
    assert p.human_dialogue['messages'][0]['status'] == 'applied'
    assert p.phase == ResearchPhase.EXECUTION_WAITING
    assert p.approval_status == 'valid'


def test_scope_change_is_a_proposal_until_explicit_confirmation(api):
    p = draft(api)
    model(api, 'restart_sources', instructions='Search a different population')
    send(api, text='请补充其他人群的研究')
    api.threads[-1].run()
    saved = api.o.get_project(p.project_id)
    assert saved.phase == ResearchPhase.COMPLETED
    assert saved.evidence and saved.approval_status == 'valid'
    proposal = saved.human_dialogue['proposal']
    response = api.client.post(f'/api/research/{p.project_id}/message/confirm',
                               json={'proposal_id': proposal['id'], 'accept': True})
    assert response.status_code == 200
    saved = api.o.get_project(p.project_id)
    assert saved.phase == ResearchPhase.BACKGROUND_RESEARCH
    assert not saved.evidence and saved.approval_status != 'valid'
    assert 'authorization' not in saved.background_agent
    assert saved.literature_review['status'] == 'previous_scope'
    assert saved.human_dialogue['research_history'][0]['evidence']
    assert saved.literature_review['draft']['report_markdown'] == 'Original answer'


def test_stale_proposal_cannot_change_new_state(api):
    p = draft(api)
    model(api, 'restart_sources')
    send(api)
    api.threads[-1].run()
    p = api.o.get_project(p.project_id)
    proposal = p.human_dialogue['proposal']
    p.constraints['new_scope'] = 'changed'
    api.o.store.save(p)
    response = api.client.post(f'/api/research/{p.project_id}/message/confirm',
                               json={'proposal_id': proposal['id'], 'accept': True})
    assert response.status_code in {400, 409, 422}
    assert api.o.get_project(p.project_id).phase == ResearchPhase.COMPLETED


def test_decline_proposal_preserves_answer_and_approval(api):
    p = draft(api)
    model(api, 'restart_sources')
    send(api)
    api.threads[-1].run()
    proposal = api.o.get_project(p.project_id).human_dialogue['proposal']
    response = api.client.post(f'/api/research/{p.project_id}/message/confirm',
                               json={'proposal_id': proposal['id'], 'accept': False})
    assert response.status_code == 200
    saved = api.o.get_project(p.project_id)
    assert not saved.human_dialogue.get('proposal')
    assert saved.phase == ResearchPhase.COMPLETED and saved.approval_status == 'valid'
    assert len(api.threads) == 1


def test_disallowed_model_action_never_mutates_workflow(api):
    model(api, 'revise_answer')
    send(api)
    api.threads[-1].run()
    p = api.o.get_project(api.project.project_id)
    assert p.phase == ResearchPhase.HYPOTHESIS_GENERATION
    assert p.human_dialogue['messages'][0]['status'] == 'not_applied'
    assert not p.literature_review


def test_budget_rejection_and_explicit_renewal_do_not_run_or_reopen(api):
    p = draft(api)
    p.budget.attempted_model_calls = p.budget.max_model_calls
    api.o.store.save(p)
    assert send(api).status_code == 409
    assert not api.o.get_project(p.project_id).human_dialogue.get('messages')
    url = f'/api/research/{p.project_id}/message/budget'
    assert api.client.post(url, json={'max_model_calls': 100}).status_code == 422
    assert api.client.post(url, json={'max_model_calls': 100, 'acknowledged': True}).status_code == 200
    assert not api.threads
    assert api.o.get_project(p.project_id).phase == ResearchPhase.COMPLETED


def test_pending_question_requires_current_id_and_does_not_grant_approval(api):
    p = api.project
    p.phase = ResearchPhase.HUMAN_SOURCE_REVIEW
    p.human_dialogue['pending'] = {'request_id': 'q1', 'prompt': 'Which population?',
                                 'resume_phase': 'BACKGROUND_RESEARCH'}
    api.o.store.save(p)
    assert send(api).status_code == 409
    assert send(api, request_id='q1').status_code == 202
    saved = api.o.get_project(p.project_id)
    assert saved.human_dialogue['responses'][0]['response'] == '请解释这项发现'
    assert saved.phase == ResearchPhase.BACKGROUND_RESEARCH
    assert saved.approval_status != 'valid'


def test_failed_model_turn_keeps_user_message(api):
    def fail(*a, **kw):
        raise TimeoutError('test timeout')
    api.monkeypatch.setattr(BackgroundAgent, 'call', fail)
    send(api)
    api.threads[-1].run()
    saved = api.o.get_project(api.project.project_id)
    entry = saved.human_dialogue['messages'][0]
    assert entry['status'] == 'failed' and entry['text'] == '请解释这项发现'
    assert api.jobs.load(saved.project_id, entry['job_id']).status == 'failed'


def test_live_document_never_uses_other_job_review_or_failed_text():
    writer = {'job_id': 'current', 'call_id': 'new', 'agent_name': 'scientific_synthesizer',
              'message_kind': 'research_draft', 'status': 'streaming', 'public_response': 'New body'}
    for wrong in ({'job_id': 'old'}, {'message_kind': 'conversation_answer'},
                  {'agent_name': 'skeptical_reviewer'}, {'status': 'failure'}):
        assert live_answer([{**writer, **wrong}], {'job_id': 'current', 'status': 'running'}) is None
    assert live_answer([writer], {'job_id': 'current', 'status': 'running'}) == writer
    assert live_answer([writer], {'job_id': 'current', 'status': 'completed'}) is None


def test_canonical_versions_not_repeated_in_timeline():
    p = {'literature_review': {'draft': {'call_id': 'new', 'report_markdown': 'New'},
                              'draft_history': [{'report_markdown': 'Old'}]}}
    entries = timeline_entries(p, [{'call_id': 'new', 'public_response': 'New'},
                                  {'call_id': 'old', 'public_response': 'Old'},
                                  {'call_id': 'discuss', 'public_response': 'Explain'}])
    assert [e['data']['public_response'] for e in entries] == ['Explain']


def test_current_reply_is_readable_without_opening_expander():
    app = AppTest.from_string('''
from src.ai_scientist.research_timeline import render_timeline
render_timeline({'project_id':'p'}, [{'status':'completed','public_response':'关键发现：证据不一致。'}], {})
''').run()
    assert not app.exception
    assert any(m.value == '关键发现：证据不一致。' for m in app.markdown)
    assert len(app.chat_message) == 1 and not app.expander


def test_unified_composer_retains_rejected_input_and_has_no_route_selector():
    app = AppTest.from_string('''
import streamlit as st
from src.ai_scientist.research_timeline import render_composer
render_composer({'project_id':'p'}, False,
  lambda payload,key: st.session_state.update(sent=payload), lambda _: None)
''').run()
    assert not app.exception and len(app.text_area) == 1 and not app.selectbox
    app.text_area[0].input('请解释不确定性').run()
    next(b for b in app.button if b.label == '发送').click().run()
    assert app.session_state['sent']['message'] == '请解释不确定性'
    assert app.text_area[0].value == '请解释不确定性'


def test_unified_answer_streams_in_place_and_finishes_without_extra_checkbox():
    app = AppTest.from_string('''
import streamlit as st
from src.ai_scientist.literature_ui import render_literature_answer
render_literature_answer({'project_id':'p','phase':'EXECUTION_WAITING','literature_review':{
    'status':'draft_ready','draft':{'report_markdown':'Saved body'}}},
    submit=lambda p: st.session_state.update(sent=p), unified=True)
''').run()
    assert not app.exception and not app.text_area and not app.checkbox
    next(b for b in app.button if b.label == '结束本轮').click().run()
    assert app.session_state['sent'] == {'action':'finish', 'acknowledged':True}


def test_no_execute_or_approve_route_exists(api):
    p = api.project
    p.phase = ResearchPhase.EXECUTION_WAITING
    p.computation = {'status': 'awaiting_approval', 'plan_id': 'code1'}
    assert 'revise_code' in allowed_actions(p)
    assert not {'execute', 'approve', 'adopt', 'budget'}.intersection(allowed_actions(p))


def test_legacy_answer_has_one_canonical_location_before_new_discussion():
    app = AppTest.from_string('''
import streamlit as st
from src.ai_scientist.research_timeline import render_timeline
render_timeline({'project_id':'p', 'literature_review':{'report_markdown':'Legacy answer'},
    'human_dialogue':{'messages':[{'id':'u','text':'Follow up','created_at':'2026-10-13'}]}},
    [], {}, render_answer=lambda: st.markdown('Canonical saved answer'))
''').run()
    assert not app.exception
    texts = [m.value for m in app.markdown]
    assert texts.count('Canonical saved answer') == 1
    assert texts.index('Canonical saved answer') < texts.index('Follow up')


def test_streamed_answer_and_previous_body_not_duplicated_in_messages():
    app = AppTest.from_string('''
from src.ai_scientist.research_timeline import render_timeline
from src.ai_scientist.literature_ui import render_literature_answer
p={'project_id':'p','literature_review':{'status':'synthesizing',
    'draft':{'report_markdown':'Old body'},'review_status':'issues_found','review_notes':['Old issue']}}
m={'call_id':'writer','public_response':'New body','status':'streaming'}
render_timeline(p,[m],{},live=m,render_answer=lambda:render_literature_answer(p,True,live=m,unified=True))
''').run()
    assert not app.exception
    assert [m.value for m in app.markdown].count('New body') == 1
    assert not next(e for e in app.expander if e.label == '上一版核查意见（新稿尚未核查）').proto.expanded


def test_worker_start_failure_never_leaves_permanent_queued_job(api):
    def fail(*a, **kw):
        raise RuntimeError('worker startup failed')
    api.monkeypatch.setattr(main_api.threading, 'Thread',
                          lambda **kwargs: type('BrokenThread', (), {'start': fail})())
    response = send(api)
    assert response.status_code >= 400
    p = api.o.get_project(api.project.project_id)
    assert p.human_dialogue['messages'][0]['status'] == 'failed'
    assert api.jobs.active_step_job(p.project_id) is None


def test_low_remaining_budget_after_interpretation_does_not_reopen_delivery(api):
    p = draft(api)
    def call(self, project, schema, payload, **kwargs):
        project.budget.attempted_model_calls = project.budget.max_model_calls
        return DialogueDecision(public_response='Will revise', action='revise_answer')
    api.monkeypatch.setattr(BackgroundAgent, 'call', call)
    send(api)
    api.threads[-1].run()
    saved = api.o.get_project(p.project_id)
    assert saved.phase == ResearchPhase.COMPLETED
    assert saved.literature_review['draft']['report_markdown'] == 'Original answer'
    assert saved.human_dialogue['messages'][0]['status'] == 'not_applied'


def test_pause_after_interpretation_preserves_request_and_offers_resume(api):
    p = draft(api)
    model(api, 'revise_answer')
    result = send(api).json()
    api.jobs.update(p.project_id, result['job_id'], lambda j: setattr(j, 'pause_requested', True))
    api.threads[-1].run()
    saved = api.o.get_project(p.project_id)
    assert saved.literature_review['draft']['report_markdown'] == 'Original answer'
    assert saved.literature_review['pending_operation'] == 'draft'
    assert saved.human_dialogue['messages'][0]['status'] == 'saved'
    assert next_research_action(saved.model_dump(mode='json'))['label'] == '继续处理已保存的请求'


@pytest.mark.parametrize('phase', [ResearchPhase.COMPLETED, ResearchPhase.HUMAN_APPROVAL])
def test_conversation_clarification_returns_to_model_not_stopped_workflow(api, phase):
    p = draft(api)
    p.phase = phase
    api.o.store.save(p)
    calls = []
    def call(self, project, schema, payload, **kwargs):
        calls.append(payload['user_message'])
        if len(calls) == 1:
            project.human_dialogue['pending'] = {'request_id':'detail1','prompt':'Which population?'}
            raise ModelInputRequired('Which population?')
        return DialogueDecision(public_response='保持原范围。', action='discuss')
    api.monkeypatch.setattr(BackgroundAgent, 'call', call)
    send(api, text='考虑扩大范围')
    api.threads[-1].run()
    saved = api.o.get_project(p.project_id)
    assert saved.human_dialogue['pending']['dialogue_origin'] == 'message1'
    response = send(api, text='先不扩大，保持原来的范围。', mid='message2', request_id='detail1')
    assert response.json()['run_mode'] == 'dialogue'
    api.threads[-1].run()
    saved = api.o.get_project(p.project_id)
    assert saved.phase == phase and not saved.human_dialogue.get('pending')
    assert saved.human_dialogue['messages'][0]['status'] == 'clarified'
    assert saved.human_dialogue['messages'][-1]['status'] == 'answered'
    assert len(calls) == 2
