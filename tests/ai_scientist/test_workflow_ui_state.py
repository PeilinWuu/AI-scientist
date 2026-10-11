"""One-action navigation tests, without Streamlit, servers or model calls."""
from copy import deepcopy

import pytest

from src.ai_scientist.schemas import ResearchPhase
from src.ai_scientist.workflow_ui_state import next_research_action


def project_at(phase='QUESTION_FORMULATION', **updates):
    return {'project_id': 'project-1', 'phase': phase, 'planning_only': False,
            'approval_status': 'valid', 'execution_capability': 'EXTERNAL_EXECUTION_REQUIRED',
            'available_tools': ['isolated_scientific_python'], **updates}


def authorized_revision(project, target='execution_code'):
    request = {'revision_plan_id': 'revision-1', 'batch_id': 'batch-1'}
    project['active_revision_plan_id'] = request['revision_plan_id']
    project['approved_revision_plans'] = [{
        'revision_plan_id': request['revision_plan_id'], 'project_id': project['project_id'],
        'created_by': 'human', 'status': 'approved', 'approved_issues': ['issue-1'],
        'target_batches': [{'batch_id': request['batch_id'], 'target': target,
                            'status': 'pending', 'issue_ids': ['issue-1']}],
    }]
    project.setdefault('computation', {})['pending_revision_request'] = request
    return project


@pytest.mark.parametrize('phase', list(ResearchPhase))
def test_every_phase_returns_exactly_one_complete_action(phase):
    action = next_research_action(project_at(phase.value))
    assert set(action) == {'kind', 'label', 'description'}
    assert all(isinstance(value, str) and value.strip() for value in action.values())


@pytest.mark.parametrize('status', ['queued', 'running'])
@pytest.mark.parametrize('phase', ['QUESTION_FORMULATION', 'HUMAN_APPROVAL', 'COMPLETED'])
def test_active_job_precedes_gates_and_transient_terminal_snapshot(status, phase):
    project = project_at(phase, human_dialogue={'pending': {'prompt': 'Input?'}})
    assert next_research_action(project, {'status': status})['kind'] == 'running'


def test_pause_acknowledgement_does_not_offer_another_action():
    result = next_research_action(project_at(), {'status': 'running', 'pause_requested': True})
    assert result['kind'] == 'running'
    assert '已收到暂停请求' in result['description']


def test_unresolved_active_job_keeps_ui_waiting():
    assert next_research_action(project_at(active_job_id='job-1'))['kind'] == 'running'
    assert next_research_action(project_at(active_job_id='job-1'),
                                {'job_id': 'older-job', 'status': 'completed'})['kind'] == 'running'


def test_completed_job_clears_matching_stale_active_id():
    assert next_research_action(project_at(active_job_id='job-1'),
                                {'job_id': 'job-1', 'status': 'completed'})['kind'] == 'continue'


def test_another_projects_job_does_not_block_this_project():
    result = next_research_action(project_at(), {'project_id': 'different', 'status': 'running'})
    assert result['kind'] == 'continue'


def test_unavailable_job_status_never_offers_another_execution():
    project = project_at('EXECUTION_WAITING', computation={'status': 'awaiting_approval'})
    result = next_research_action(project, {'status': 'unavailable'})
    assert result['kind'] == 'running'
    assert '暂时无法读取' in result['description']


@pytest.mark.parametrize('phase', ['COMPLETED', 'FAILED', 'CANCELLED'])
def test_terminal_state_precedes_stale_question(phase):
    project = project_at(phase, human_dialogue={'pending': {'prompt': 'Stale question'}})
    assert next_research_action(project)['kind'] == phase.lower()


@pytest.mark.parametrize('phase', ['QUESTION_FORMULATION', 'HUMAN_APPROVAL', 'HUMAN_SOURCE_REVIEW',
                                  'HUMAN_REVISION_REVIEW', 'EXECUTION_WAITING', 'EXECUTION',
                                  'HUMAN_INTERVENTION_REQUIRED'])
def test_pending_question_blocks_all_other_actions(phase):
    project = project_at(phase, human_dialogue={'pending': {'request_id': 'ask-1', 'prompt': 'Choose data'}},
                         computation={'status': 'awaiting_approval', 'awaiting_assessment': {'run_id': 'run-1'}},
                         executor_binding='legacy', execution_capability='INTERNAL_EXECUTABLE')
    assert next_research_action(project)['kind'] == 'reply'


@pytest.mark.parametrize(('phase', 'kind'), [
    ('SEARCH_PLAN_REVIEW', 'evidence_review'), ('HUMAN_SOURCE_REVIEW', 'evidence_review'),
    ('HUMAN_REVISION_REVIEW', 'revision_review'), ('HUMAN_APPROVAL', 'approval'),
    ('HUMAN_INTERVENTION_REQUIRED', 'intervention'),
])
def test_human_gates_are_not_generic_continue_buttons(phase, kind):
    assert next_research_action(project_at(phase))['kind'] == kind


@pytest.mark.parametrize('phase', ['EXECUTION_WAITING', 'EXECUTION'])
def test_bound_executor_requires_explicit_execution_action(phase):
    project = project_at(phase, executor_binding='damped_oscillator_v1',
                         execution_capability='INTERNAL_EXECUTABLE')
    result = next_research_action(project)
    assert result['kind'] == 'execute_bound'
    if phase == 'EXECUTION_WAITING':
        assert result['label'] == '准备项目内执行'
        assert '不会运行代码' in result['description']
    else:
        assert result['label'] == '确认并开始项目内执行'


def test_waiting_assessment_takes_precedence_over_stale_approval():
    project = project_at('EXECUTION_WAITING', computation={
        'status': 'awaiting_approval', 'awaiting_assessment': {'run_id': 'existing-result'}})
    result = next_research_action(project)
    assert result['kind'] == 'resume_assessment'
    assert '不会重复执行计算' in result['description']


def test_computation_approval_has_no_generic_continue():
    project = project_at('EXECUTION_WAITING', computation={'status': 'awaiting_approval'})
    assert next_research_action(project)['kind'] == 'computation_approval'


def test_planning_only_does_not_offer_stale_computation_execution():
    project = project_at('EXECUTION_WAITING', planning_only=True,
                         computation={'status': 'awaiting_approval'})
    result = next_research_action(project)
    assert result['kind'] == 'continue'
    assert '不会执行实验' in result['description']


def test_waiting_without_computation_needs_external_data():
    assert next_research_action(project_at('EXECUTION_WAITING', available_tools=[]))['kind'] == 'external_data'


@pytest.mark.parametrize('approval', ['stale', 'pending', 'deferred', None])
def test_general_computation_requires_valid_approval(approval):
    project = project_at('EXECUTION_WAITING', approval_status=approval)
    assert next_research_action(project)['kind'] == 'intervention'


def test_first_computation_preparation_is_not_execution():
    result = next_research_action(project_at('EXECUTION_WAITING'))
    assert result['kind'] == 'prepare_computation'
    assert '此操作不执行代码' in result['description']


def test_iterated_round_is_prepared_only_if_model_requested_it():
    project = project_at('EXECUTION_WAITING', computation={
        'status': 'iteration_proposed', 'rounds': [{'assessment': {'decision': 'iterate'}}]})
    assert next_research_action(project)['kind'] == 'prepare_computation'
    project['computation']['rounds'][0]['assessment']['decision'] = 'stop'
    assert next_research_action(project)['kind'] == 'intervention'


def test_exhausted_scientific_rounds_do_not_present_a_known_failing_button():
    project = project_at('EXECUTION_WAITING', computation={
        'rounds': [{'assessment': {'decision': 'iterate'}}] * 2})
    assert next_research_action(project)['kind'] == 'intervention'


@pytest.mark.parametrize('target', ['execution_code', 'result_table', 'figure', 'reproducibility_bundle'])
def test_approved_artifact_revision_can_prepare_despite_stale_global_approval_and_two_rounds(target):
    project = project_at('EXECUTION_WAITING', approval_status='stale', computation={
        'status': 'review_required', 'rounds': [{'assessment': {'decision': 'stop'}}] * 2})
    authorized_revision(project, target)
    result = next_research_action(project)
    assert result['kind'] == 'prepare_computation'
    assert result['label'] == '生成修订计算方案'


@pytest.mark.parametrize('problem', ['missing-plan', 'inactive-plan', 'other-project', 'nonhuman',
                                    'completed-plan', 'completed-batch', 'unapproved-issue',
                                    'empty-issues', 'wrong-target', 'wrong-batch', 'empty-request'])
def test_pending_revision_request_cannot_forge_authority(problem):
    project = authorized_revision(project_at('EXECUTION_WAITING', approval_status='stale'))
    plan = project['approved_revision_plans'][0]
    batch = plan['target_batches'][0]
    if problem == 'missing-plan':
        project['approved_revision_plans'] = []
    elif problem == 'inactive-plan':
        project['active_revision_plan_id'] = 'different'
    elif problem == 'other-project':
        plan['project_id'] = 'different'
    elif problem == 'nonhuman':
        plan['created_by'] = 'model'
    elif problem == 'completed-plan':
        plan['status'] = 'completed'
    elif problem == 'completed-batch':
        batch['status'] = 'completed'
    elif problem == 'unapproved-issue':
        plan['approved_issues'] = []
    elif problem == 'empty-issues':
        batch['issue_ids'] = []
    elif problem == 'wrong-target':
        batch['target'] = 'analysis_plan'
    elif problem == 'wrong-batch':
        batch['batch_id'] = 'different'
    else:
        project['computation']['pending_revision_request'] = {}
    assert next_research_action(project)['kind'] == 'intervention'


@pytest.mark.parametrize('status', ['needs_input', 'unsupported'])
def test_explicit_preparation_recovery_remains_available_after_supplementing_inputs(status):
    project = project_at('EXECUTION_WAITING', computation={'status': status})
    result = next_research_action(project)
    assert result['kind'] == 'prepare_computation'
    assert '补充' in result['label']
    assert '请先' in result['description']
    # Uploading an asset does not change the saved computation status. The UI
    # must keep a reachable explicit retry, not permanently strand the project.
    project['research_assets'] = [{'filename': 'new_input.csv', 'purpose': 'data', 'parsing_status': 'parsed'}]
    assert next_research_action(project)['kind'] == 'prepare_computation'


def test_needs_input_after_one_round_allows_explicit_replanning_but_not_execution():
    project = project_at('EXECUTION_WAITING', computation={
        'status': 'needs_input', 'rounds': [{'assessment': {'decision': 'needs_input'}}]})
    result = next_research_action(project)
    assert result['kind'] == 'prepare_computation'
    assert '核验审批与输入变化' in result['description']
    project['computation']['rounds'].append({'assessment': {'decision': 'needs_input'}})
    assert next_research_action(project)['kind'] == 'intervention'


def test_supplemental_input_recovery_does_not_bypass_pending_reply_or_invalid_approval():
    project = project_at('EXECUTION_WAITING', computation={'status': 'needs_input'})
    project['human_dialogue'] = {'pending': {'prompt': 'Please supply data'}}
    assert next_research_action(project)['kind'] == 'reply'
    project['human_dialogue'] = {}
    project['approval_status'] = 'stale'
    assert next_research_action(project)['kind'] == 'intervention'


def test_human_intervention_for_missing_computation_inputs_has_explicit_recovery():
    project = project_at('HUMAN_INTERVENTION_REQUIRED', computation={
        'status': 'needs_input', 'rounds': [{'assessment': {'decision': 'needs_input'}}]})
    assert next_research_action(project)['kind'] == 'prepare_computation'
    project['computation']['awaiting_assessment'] = {'run_id': 'completed-run'}
    assert next_research_action(project)['kind'] == 'resume_assessment'


@pytest.mark.parametrize('change', [
    {'approval_status': 'stale'}, {'planning_only': True},
    {'executor_binding': 'legacy'}, {'available_tools': []},
    {'computation': {'status': 'unsupported'}},
    {'computation': {'status': 'awaiting_approval'}},
])
def test_other_human_interventions_do_not_gain_computation_recovery(change):
    project = project_at('HUMAN_INTERVENTION_REQUIRED', computation={'status': 'needs_input'})
    project.update(change)
    assert next_research_action(project)['kind'] == 'intervention'


def test_scoped_revision_cannot_bypass_global_approval_in_human_intervention():
    project = authorized_revision(project_at('HUMAN_INTERVENTION_REQUIRED',
        approval_status='stale', computation={'status': 'needs_input'}))
    assert next_research_action(project)['kind'] == 'intervention'


def test_new_project_invites_explicit_start_not_misleading_continue():
    result = next_research_action(project_at('INTAKE'))
    assert result['kind'] == 'continue'
    assert result['label'] == '开始研究'
    assert '尚未开始研究' in result['description']


def test_answered_plan_question_can_resume_preparation():
    project = project_at('EXECUTION_WAITING', human_dialogue={'responses': [{'response': 'Simulate'}]},
                         computation={'status': 'needs_input', 'awaiting_model_operation': 'computation_plan'})
    assert next_research_action(project)['kind'] == 'prepare_computation'


def test_failed_job_is_recoverable_without_marking_project_failed():
    result = next_research_action(project_at(), {'status': 'failed'})
    assert result['kind'] == 'continue'
    assert '重试' in result['label']
    assert next_research_action(project_at('EXECUTION_WAITING'), {'status': 'failed'})['kind'] == 'prepare_computation'


@pytest.mark.parametrize('phase', ['EXECUTION', 'EXECUTION_WAITING'])
def test_inconsistent_binding_does_not_switch_execution_mechanisms(phase):
    project = project_at(phase, executor_binding='legacy', execution_capability='EXTERNAL_EXECUTION_REQUIRED')
    assert next_research_action(project)['kind'] == 'intervention'


def test_resolver_does_not_mutate_project_or_job():
    project = authorized_revision(project_at('EXECUTION_WAITING'))
    job = {'status': 'completed', 'result': {'phase': 'EXECUTION_WAITING'}}
    before = deepcopy((project, job))
    next_research_action(project, job)
    assert (project, job) == before
