"""A provider's input request pauses background work before its draft action."""
from types import SimpleNamespace

import pytest

from src.ai_scientist.background_agent import (
    BackgroundAction, BackgroundAgent, BasisProposalReview, ResearchBasis, fingerprint,
)
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import ResearchPhase, utc_now
from src.ai_scientist.structured_client import StructuredCallMetadata, StructuredCallResult


@pytest.fixture
def project(tmp_path):
    orchestrator = ResearchOrchestrator(tmp_path)
    value = orchestrator.create_project('需要验证测量误差，先澄清数据来源', research_policy_version=2)
    value.phase = ResearchPhase.BACKGROUND_RESEARCH
    orchestrator.store.save(value)
    return orchestrator, value


def scope():
    return ResearchBasis(external_information='optional', rationale='本研究可以先核对用户提供的数据与约束。',
                         information_gaps=[], validation_basis=['后续仍需要实际验证'],
                         initial_queries=[], stopping_rule='明确方法依据或报告未解决的信息缺口')


def install_provider(monkeypatch, responses):
    calls = []
    queue = iter(responses)
    class FakeClient:
        def __init__(self, registry):
            self.registry = registry
            self.provider_responses = []
            self.http_client = None
        def _get_client(self):
            return SimpleNamespace(with_options=lambda **kwargs: object())
        def call(self, role, instructions, payload, schema):
            calls.append((role, schema))
            output, question = next(queue)
            now = utc_now()
            return StructuredCallResult(output, StructuredCallMetadata(
                agent_name=role, requested_model='test-qwen', actual_model='test-qwen',
                fallback_used=False, started_at=now, finished_at=now, model_calls=1,
                attempted_calls=1, successful_calls=1, human_input_request=question,
                public_response=question or '根据实际资料继续研究。', call_id=f'call-{len(calls)}'))
    monkeypatch.setattr('src.ai_scientist.background_agent.StructuredQwenClient', FakeClient)
    return calls


def authorize_existing_scope(orchestrator, project):
    project.background_agent = {
        'scope': scope().model_dump(), 'scope_id': 'scope-test', 'fingerprint': fingerprint(project),
        'authorization': {'scope_id': 'scope-test', 'allow_network': True, 'action_limit': 12},
        'status': 'ready', 'sources': [], 'assessments': {}, 'observations': [],
    }
    orchestrator.store.save(project)


def test_scope_question_keeps_original_phase_and_does_not_publish_scope(project, monkeypatch):
    orchestrator, value = project
    calls = install_provider(monkeypatch, [(scope(), '请先说明这些数据是实测还是模拟。')])
    result = orchestrator.run_next_step(value.project_id)
    saved = orchestrator.get_project(value.project_id)
    assert len(calls) == 1
    assert result['stage_status'] == 'awaiting_model_requested_input'
    assert saved.phase == ResearchPhase.BACKGROUND_RESEARCH
    assert not saved.background_agent.get('scope')
    assert saved.human_dialogue['pending']['prompt'] == '请先说明这些数据是实测还是模拟。'
    assert saved.budget.attempted_model_calls == saved.budget.successful_model_calls == 1
    assert saved.budget.failed_model_calls == 0


def test_action_question_prevents_tool_execution_without_failed_billing(project, monkeypatch):
    orchestrator, value = project
    authorize_existing_scope(orchestrator, value)
    proposed = BackgroundAction(action='search_web', purpose='检索适用测量方法', query='measurement errors')
    calls = install_provider(monkeypatch, [(proposed, '你的数据采样频率是多少？')])
    performed = []
    monkeypatch.setattr(BackgroundAgent, 'perform', lambda *args: performed.append(args))
    result = orchestrator.run_next_step(value.project_id)
    saved = orchestrator.get_project(value.project_id)
    assert len(calls) == 1
    assert performed == []
    assert saved.background_agent['observations'] == []
    assert result['stage_status'] == 'awaiting_model_requested_input'
    assert saved.phase == ResearchPhase.BACKGROUND_RESEARCH
    assert saved.budget.attempted_model_calls == saved.budget.successful_model_calls == 1
    assert saved.budget.failed_model_calls == 0


def test_finish_review_question_preserves_pause_and_never_adopts_proposal(project, monkeypatch):
    orchestrator, value = project
    authorize_existing_scope(orchestrator, value)
    finish = BackgroundAction(action='finish', purpose='提交已限定范围的方法依据', summary='尚需后续计算验证。')
    review = BasisProposalReview(decision='ready_for_human', rationale='待用户说明样本采集条件后才能判断。')
    calls = install_provider(monkeypatch, [(finish, ''), (review, '采样条件是否在全部样本中保持一致？')])
    result = orchestrator.run_next_step(value.project_id)
    saved = orchestrator.get_project(value.project_id)
    assert [schema for _, schema in calls] == [BackgroundAction, BasisProposalReview]
    assert saved.phase == ResearchPhase.BACKGROUND_RESEARCH
    assert result['stage_status'] == 'awaiting_model_requested_input'
    assert saved.background_agent['status'] != 'awaiting_adoption'
    assert not saved.background_agent.get('proposal_id')
    assert saved.background_agent['observations'][0]['status'] == 'awaiting_input'
    assert saved.budget.attempted_model_calls == saved.budget.successful_model_calls == 2
    assert saved.budget.failed_model_calls == 0
    assert saved.human_dialogue['pending']['agent_name'] == 'skeptical_reviewer'
