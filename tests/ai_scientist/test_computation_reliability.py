import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from src.ai_scientist.computation import ComputationAssessment, ComputationPlan, ComputationService
from src.ai_scientist.exceptions import ModelInputRequired
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import ApprovedRevisionPlan, EvidenceItem, ResearchPhase, RevisionTargetBatch, utc_now
from src.ai_scientist.structured_client import StructuredCallMetadata
from src.ai_scientist.tools.computation_sandbox import ComputationSandbox


def computation_plan():
    return ComputationPlan(decision='execute', rationale='Calculate requested values', method='sum',
                           assumptions=[], input_files=[], expected_outputs=['result.json', 'values.csv'],
                           required_metrics=['total'], validation_checks=['finite result'],
                           stopping_rule='answer and delivery checked', code='print("real source")')


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_SCIENTIST_ENABLE_COMPUTATION', '1')
    monkeypatch.setattr('src.ai_scientist.computation.runtime_status', lambda: {'available': True})
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project('Compute total of 1 and 2', planning_only=False)
    project.phase, project.approval_status = ResearchPhase.EXECUTION_WAITING, 'valid'
    orchestrator.store.save(project)
    return ComputationService(orchestrator), project


def approve_revision(project):
    batch = RevisionTargetBatch(target='execution_requirements', issue_ids=['missing-code'],
                                instructions=['Deliver the exact executed code'],
                                completion_criteria=['Source checksum matches executed program'])
    # This subtask can be tested while the parent adds the new schema targets.
    batch = batch.model_copy(update={'target': 'execution_code'})
    revision = ApprovedRevisionPlan(project_id=project.project_id, review_version=1, revision_cycle=1,
                                    approved_issues=['missing-code'], target_batches=[batch])
    project.approved_revision_plans = [revision]
    project.active_revision_plan_id = revision.revision_plan_id
    return {'revision_plan_id': revision.revision_plan_id, 'batch_id': batch.batch_id}


def test_context_contains_prior_evidence_and_feedback_without_receipt_fingerprint_churn(service):
    service, project = service
    project.evidence.append(EvidenceItem(title='Previously read paper', source_type='paper', summary='Source text'))
    project.revision_feedback.append('Please account for prior evidence')
    context, _, fingerprint = service.context(project)
    assert 'Previously read paper' in json.dumps(context)
    assert 'Please account for prior evidence' in json.dumps(context)
    project.stage_messages.append('A model call finished')
    project.phase = ResearchPhase.DATA_ANALYSIS
    assert service.context(project)[2] == fingerprint
    project.revision_feedback.append('Additional scientific requirement')
    assert service.context(project)[2] != fingerprint


def test_saved_human_approved_batch_unlocks_replanning_not_execution(service, monkeypatch):
    service, project = service
    project.computation = {'status': 'review_required', 'rounds': [
        {'assessment': {'decision': 'stop'}}, {'assessment': {'decision': 'stop'}}]}
    revision_request = approve_revision(project)
    captured = []
    monkeypatch.setattr(service, 'call', lambda *args: captured.append(args[2]) or computation_plan())
    state = service.prepare(project.project_id, project=project, revision_request=revision_request)
    assert state['status'] == 'awaiting_approval'
    assert state['revision_request']['batch_id'] == revision_request['batch_id']
    assert captured[0]['approved_revision_request']['instructions'] == ['Deliver the exact executed code']
    assert project.approved_revision_plans[0].target_batches[0].status == 'pending'


@pytest.mark.parametrize('problem', ['unapproved', 'inactive', 'completed', 'unrelated'])
def test_cannot_forge_or_replay_revision_authority(service, problem):
    service, project = service
    request = approve_revision(project)
    if problem == 'unapproved':
        project.approved_revision_plans[0].approved_issues = []
    elif problem == 'inactive':
        project.active_revision_plan_id = None
    elif problem == 'completed':
        project.approved_revision_plans[0].target_batches[0].status = 'completed'
    else:
        project.approved_revision_plans[0].target_batches[0].target = 'analysis_plan'
    with pytest.raises(ValueError, match='approved'):
        service.revision_authorization(project, request)


def test_changed_revision_instructions_invalidate_code_approval(service, monkeypatch):
    service, project = service
    request = approve_revision(project)
    monkeypatch.setattr(service, 'call', lambda *args: computation_plan())
    pending = service.prepare(project.project_id, project=project, revision_request=request)
    project.approved_revision_plans[0].target_batches[0].instructions = ['A different method']
    monkeypatch.setattr(service.o, 'get_project', lambda *args: project)
    monkeypatch.setattr(ComputationSandbox, 'execute', lambda *args: pytest.fail('Must not execute stale approval'))
    with pytest.raises(ValueError, match='requirements changed'):
        service.execute(project.project_id, pending['plan_id'])


def test_scoped_revision_does_not_require_falsifying_global_approval(service, monkeypatch):
    service, project = service
    request = approve_revision(project)
    project.approval_status = 'stale'
    monkeypatch.setattr(service, 'call', lambda *args: computation_plan())
    state = service.prepare(project.project_id, project=project, revision_request=request)
    assert state['status'] == 'awaiting_approval'
    assert project.approval_status == 'stale'
    service.check(project)


def test_model_requested_input_stores_draft_not_executable_plan(service, monkeypatch):
    service, project = service
    now = utc_now()
    metadata = StructuredCallMetadata(agent_name='analyst', requested_model='test-double', actual_model='test-double',
        fallback_used=False, started_at=now, finished_at=now, attempted_calls=1, successful_calls=1,
        public_response='请提供数据的测量单位。', human_input_request='数据的测量单位是什么？')
    client = SimpleNamespace(http_client=None, call=lambda *args: SimpleNamespace(value=computation_plan(), metadata=metadata))
    monkeypatch.setattr('src.ai_scientist.computation.StructuredQwenClient', lambda *args: client)
    with pytest.raises(ModelInputRequired):
        service.prepare(project.project_id)
    saved = service.o.get_project(project.project_id)
    assert saved.computation['status'] == 'needs_input'
    assert 'plan_id' not in saved.computation
    assert saved.human_dialogue['pending']['prompt'] == metadata.human_input_request
    assert saved.budget.used_model_calls == 1
    assert saved.budget.failed_model_calls == 0
    assert [artifact.artifact_type for artifact in saved.artifacts].count('pending_model_draft') == 1
    assert not any(artifact.artifact_type == 'computation_plan' for artifact in saved.artifacts)


def paused_assessment(service, monkeypatch):
    service, project = service
    root = service.o.store.project_dir(project.project_id)
    executions = []
    def execute(*args):
        executions.append(1)
        directory = root / 'computation' / 'completed_run'
        (directory / 'output').mkdir(parents=True)
        program = directory / 'program.py'
        program.write_text(computation_plan().code)
        artifacts = []
        for name, text in {'result.json': '{"metrics":{"total":3}}', 'values.csv': 'value\n1\n2\n'}.items():
            path = directory / 'output' / name
            path.write_text(text)
            artifacts.append({'relative_path': str(path.relative_to(root)),
                              'checksum_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        return {'run_id': 'completed_run', 'status': 'success', 'result': {'metrics': {'total': 3}},
                'artifacts': artifacts, 'code_path': str(program.relative_to(root)),
                'code_sha256': hashlib.sha256(program.read_bytes()).hexdigest()}
    def call(*args):
        if args[3] == ComputationPlan:
            return computation_plan()
        args[0].human_dialogue['pending'] = {'request_id': 'unit', 'prompt': 'Unit?'}
        raise ModelInputRequired('Unit?')
    monkeypatch.setattr(service, 'call', call)
    monkeypatch.setattr(ComputationSandbox, 'execute', execute)
    pending = service.prepare(project.project_id)
    with pytest.raises(ModelInputRequired):
        service.execute(project.project_id, pending['plan_id'])
    saved = service.o.get_project(project.project_id)
    assert saved.computation['attempts'][-1]['status'] == 'success'
    assert saved.computation['awaiting_assessment']['run_id'] == 'completed_run'
    assert saved.computation['status'] == 'needs_input'
    assert not saved.computation['rounds']
    return service, saved, root, executions


def test_answer_resumes_only_assessment_never_reexecutes(service, monkeypatch):
    service, project, root, executions = paused_assessment(service, monkeypatch)
    with pytest.raises(ValueError, match='pending model question'):
        service.resume_assessment(project.project_id)
    project.human_dialogue.pop('pending')
    project.human_dialogue['responses'] = [{'request_id': 'unit', 'answer': 'Dimensionless'}]
    service.o.store.save(project)
    captured = []
    monkeypatch.setattr(service, 'call', lambda *args: captured.append(args[2]) or ComputationAssessment(
        decision='stop', addresses_question=True, interpretation='Dimensionless sum is 3', limitations=[], next_question=''))
    state = service.resume_assessment(project.project_id)
    assert len(executions) == 1
    assert len(state['rounds']) == 1
    assert 'awaiting_assessment' not in state
    assert 'Dimensionless' in json.dumps(captured)
    saved = service.o.get_project(project.project_id)
    assert saved.phase == ResearchPhase.DATA_ANALYSIS
    assert service.context(saved)[2] == state['input_fingerprint']
    with pytest.raises(ValueError):
        service.resume_assessment(project.project_id)
    assert len(executions) == 1


@pytest.mark.parametrize('change', ['inputs', 'artifact'])
def test_resume_refuses_changed_inputs_or_artifacts_without_running(service, monkeypatch, change):
    service, project, root, executions = paused_assessment(service, monkeypatch)
    project.human_dialogue.pop('pending')
    if change == 'inputs':
        project.objective = 'A new research question'
    else:
        (root / 'computation/completed_run/output/values.csv').write_text('value\n999\n')
    service.o.store.save(project)
    monkeypatch.setattr(service, 'call', lambda *args: pytest.fail('Do not assess stale inputs'))
    with pytest.raises(ValueError):
        service.resume_assessment(project.project_id)
    assert len(executions) == 1


def test_scientific_stop_does_not_hide_delivery_failure(service, monkeypatch):
    service, project = service
    captured = []
    def model_call(*args):
        captured.append(args[2])
        if args[3] == ComputationPlan:
            return computation_plan()
        return ComputationAssessment(decision='stop', addresses_question=True, interpretation='Sum is 3',
                                     limitations=[], next_question='')
    monkeypatch.setattr(service, 'call', model_call)
    monkeypatch.setattr(ComputationSandbox, 'execute', lambda *args: {
        'run_id': 'missing_run', 'status': 'success', 'result': {'metrics': {'total': 3}}, 'artifacts': []})
    state = service.prepare(project.project_id)
    state = service.execute(project.project_id, state['plan_id'])
    assert state['completion_status']['science'] == 'sufficient'
    assert state['completion_status']['delivery'] == 'needs_revision'
    assert state['completion_status']['acceptance'] == 'independent_review_required'
    assert captured[-1]['delivery_validation']['status'] == 'needs_revision'
    assert state['rounds'][-1]['delivery_validation']['issues']


def test_assessment_receives_real_cells_and_platform_source(service, monkeypatch):
    service, project = service
    root = service.o.store.project_dir(project.project_id)
    captured = []
    def model_call(*args):
        captured.append(args[2])
        if args[3] == ComputationPlan:
            return computation_plan()
        return ComputationAssessment(decision='stop', addresses_question=True, interpretation='Sum is 3',
                                     limitations=[], next_question='')
    def run(*args):
        directory = root / 'computation' / 'run_1'
        (directory / 'output').mkdir(parents=True)
        program = directory / 'program.py'
        program.write_text(computation_plan().code)
        artifacts = []
        for name, text in {'result.json': '{"metrics":{"total":3}}', 'values.csv': 'value\n1\n2\n'}.items():
            path = directory / 'output' / name
            path.write_text(text)
            artifacts.append({'relative_path': str(path.relative_to(root)),
                              'checksum_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        return {'run_id': 'run_1', 'status': 'success', 'result': {'metrics': {'total': 3}},
                'artifacts': artifacts, 'code_path': str(program.relative_to(root)),
                'code_sha256': hashlib.sha256(program.read_bytes()).hexdigest()}
    monkeypatch.setattr(service, 'call', model_call)
    monkeypatch.setattr(ComputationSandbox, 'execute', run)
    pending = service.prepare(project.project_id)
    state = service.execute(project.project_id, pending['plan_id'])
    payload = captured[-1]
    assert state['delivery_validation']['status'] == 'complete'
    table = next(item for item in payload['actual_artifacts'] if item.get('filename') == 'values.csv')
    assert json.loads(table['preview'])['rows'] == [['1'], ['2']]
    assert any(item['role'] == 'executed_program' for item in payload['actual_artifacts'])


@pytest.mark.skipif(os.getenv('RUN_NAMESPACE_TESTS') != '1', reason='Requires Linux namespaces')
def test_real_runner_packages_actual_source_without_claiming_seed_verified(tmp_path):
    code = '''import json
import numpy as np
rng = np.random.default_rng(888)
json.dump({'metrics': {'sample': int(rng.integers(10))}, 'actual_seed': 888}, open('result.json', 'w'))
'''
    audit = ComputationSandbox(tmp_path).execute(code, [], ['result.json'], seed=123)
    assert audit['status'] == 'success', audit
    assert audit['runner_seed'] == 123
    assert audit['result']['actual_seed'] == 888
    assert audit['seed_audit']['code_seed_verified'] is False
    packaged_source = audit['artifact_package'][0]
    assert packaged_source['role'] == 'executed_program'
    assert (tmp_path / packaged_source['relative_path']).read_text() == code
    assert packaged_source['checksum_sha256'] == hashlib.sha256(code.encode()).hexdigest()
