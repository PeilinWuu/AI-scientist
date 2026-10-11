"""Input recovery must expose new code approval without bypassing human gates."""
import hashlib

import pytest

from src.ai_scientist.computation import ComputationAssessment, ComputationPlan, ComputationService
from src.ai_scientist.exceptions import BudgetExceededError
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import ResearchPhase
from src.ai_scientist.tools.computation_sandbox import ComputationSandbox


def plan(decision='execute'):
    return ComputationPlan(decision=decision, rationale='Use the newly supplied condition',
                           method='sum of supplied values', assumptions=[], input_files=[],
                           expected_outputs=['result.json'], required_metrics=['total'],
                           validation_checks=['finite sum'], stopping_rule='evaluate result',
                           code='import json\njson.dump({"metrics":{"total":3}}, open("result.json", "w"))')


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_SCIENTIST_ENABLE_COMPUTATION', '1')
    monkeypatch.setattr('src.ai_scientist.computation.runtime_status', lambda: {'available': True})
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project('Calculate a supplied sum', planning_only=False)
    project.phase = ResearchPhase.HUMAN_INTERVENTION_REQUIRED
    project.approval_status = 'valid'
    service = ComputationService(orchestrator)
    project.computation = {'status': 'needs_input', 'rounds': [
        {'assessment': {'decision': 'needs_input'}}], 'input_fingerprint': service.context(project)[2]}
    project.human_dialogue['responses'] = [{'request_id': 'values', 'answer': 'Use dimensionless values 1 and 2.'}]
    orchestrator.store.save(project)
    return service, project


def test_supplied_input_recovers_to_new_plan_approval_and_exact_execution(recovery, monkeypatch):
    service, project = recovery
    executions = []
    def call(*args):
        if args[3] == ComputationPlan:
            return plan()
        return ComputationAssessment(decision='stop', addresses_question=True,
                                     interpretation='Total is 3', limitations=[], next_question='')
    def execute(sandbox, code, assets, expected, seed):
        executions.append(code)
        run = sandbox.root / 'computation' / 'recovery_run'
        (run / 'output').mkdir(parents=True)
        program = run / 'program.py'
        program.write_text(code)
        output = run / 'output' / 'result.json'
        output.write_text('{"metrics":{"total":3}}')
        return {'status': 'success', 'run_id': 'recovery_run', 'result': {'metrics': {'total': 3}},
                'code_path': str(program.relative_to(sandbox.root)),
                'code_sha256': hashlib.sha256(program.read_bytes()).hexdigest(),
                'artifacts': [{'relative_path': str(output.relative_to(sandbox.root)),
                               'checksum_sha256': hashlib.sha256(output.read_bytes()).hexdigest()}]}
    monkeypatch.setattr(service, 'call', call)
    monkeypatch.setattr(ComputationSandbox, 'execute', execute)
    pending = service.prepare(project.project_id)
    saved = service.o.get_project(project.project_id)
    assert saved.phase == ResearchPhase.EXECUTION_WAITING
    assert pending['status'] == 'awaiting_approval'
    assert saved.approval_status == 'valid'
    assert executions == []
    with pytest.raises(ValueError, match='stale'):
        service.execute(project.project_id, 'old-or-unapproved-plan')
    assert executions == []
    result = service.execute(project.project_id, pending['plan_id'])
    assert executions == [plan().code]
    assert result['rounds'][-1]['assessment']['addresses_question']
    assert service.o.get_project(project.project_id).phase == ResearchPhase.DATA_ANALYSIS


@pytest.mark.parametrize('decision', ['needs_input', 'unsupported'])
def test_nonexecutable_proposal_does_not_clear_intervention(recovery, monkeypatch, decision):
    service, project = recovery
    monkeypatch.setattr(service, 'call', lambda *args: plan(decision))
    state = service.prepare(project.project_id)
    assert state['status'] == decision
    assert service.o.get_project(project.project_id).phase == ResearchPhase.HUMAN_INTERVENTION_REQUIRED


@pytest.mark.parametrize('reason', ['unrelated_intervention', 'unanswered_question', 'stale_approval', 'no_new_input'])
def test_recovery_cannot_bypass_other_human_gates(recovery, monkeypatch, reason):
    service, project = recovery
    if reason == 'unrelated_intervention':
        project.computation['status'] = 'review_required'
    elif reason == 'unanswered_question':
        project.human_dialogue['pending'] = {'request_id': 'source', 'prompt': 'Which measurements?'}
    elif reason == 'stale_approval':
        project.approval_status = 'stale'
    else:
        project.computation['input_fingerprint'] = service.context(project)[2]
    service.o.store.save(project)
    monkeypatch.setattr(service, 'call', lambda *args: pytest.fail('Must not call model through unresolved gate'))
    with pytest.raises(ValueError):
        service.prepare(project.project_id)
    assert service.o.get_project(project.project_id).phase == ResearchPhase.HUMAN_INTERVENTION_REQUIRED


def test_exhausted_budget_leaves_intervention_and_previous_state(recovery, monkeypatch):
    service, project = recovery
    project.budget.used_model_calls = project.budget.max_model_calls
    service.o.store.save(project)
    monkeypatch.setattr('src.ai_scientist.computation.StructuredQwenClient',
                        lambda *args: pytest.fail('Budget must be checked before client creation'))
    with pytest.raises(BudgetExceededError):
        service.prepare(project.project_id)
    saved = service.o.get_project(project.project_id)
    assert saved.phase == ResearchPhase.HUMAN_INTERVENTION_REQUIRED
    assert saved.computation['status'] == 'needs_input'


def test_invalid_proposal_does_not_clear_intervention(recovery, monkeypatch):
    service, project = recovery
    monkeypatch.setattr(service, 'call', lambda *args: plan().model_copy(update={'input_files': ['unregistered.csv']}))
    with pytest.raises(ValueError, match='unregistered'):
        service.prepare(project.project_id)
    assert service.o.get_project(project.project_id).phase == ResearchPhase.HUMAN_INTERVENTION_REQUIRED
