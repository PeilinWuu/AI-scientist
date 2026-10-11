import json
import os
from pathlib import Path

import pytest

from src.ai_scientist.computation import ComputationService, ComputationPlan, ComputationAssessment
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import ResearchPhase
from src.ai_scientist.tools.computation_sandbox import ComputationSandbox


def plan(code='pass'):
    return ComputationPlan(decision='execute', rationale='Explicit numerical calculation',
                           method='sum of squares', assumptions=[], input_files=[],
                           expected_outputs=['result.json'], required_metrics=['sum_squares'],
                           validation_checks=['nonnegative sum'], stopping_rule='one calculation', code=code)


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_SCIENTIST_ENABLE_COMPUTATION', '1')
    monkeypatch.setattr('src.ai_scientist.computation.runtime_status', lambda: {'available': True})
    o = ResearchOrchestrator(tmp_path)
    project = o.create_project('Compute sum of squares for integers 1 through 20.', planning_only=False)
    project.phase = ResearchPhase.EXECUTION_WAITING
    project.approval_status = 'valid'
    o.store.save(project)
    return ComputationService(o), project.project_id


def test_approval_rejects_changed_question_and_duplicate_execution(service, monkeypatch):
    service, pid = service
    monkeypatch.setattr(service, 'call', lambda *args: plan())
    pending = service.prepare(pid)
    with pytest.raises(ValueError, match='stale'):
        service.execute(pid, 'wrong')
    project = service.o.get_project(pid)
    project.objective = 'A different question'
    service.o.store.save(project)
    with pytest.raises(ValueError, match='changed'):
        service.execute(pid, pending['plan_id'])


def test_success_returns_to_review_with_traceable_round(service, monkeypatch):
    service, pid = service
    assessment = ComputationAssessment(decision='stop', addresses_question=True,
                                      interpretation='Answer computed', limitations=[], next_question='')
    monkeypatch.setattr(service, 'call', lambda *args: plan() if args[3] == ComputationPlan else assessment)
    monkeypatch.setattr(ComputationSandbox, 'execute', lambda *args: {
        'status': 'success', 'result': {'metrics': {'sum_squares': 2870}}, 'artifacts': []})
    pending = service.prepare(pid)
    service.execute(pid, pending['plan_id'])
    result = service.o.get_project(pid)
    assert result.phase == ResearchPhase.DATA_ANALYSIS
    assert result.computation['rounds'][0]['assessment']['addresses_question']
    assert result.claims[-1].supporting_evidence_ids == [result.evidence[-1].evidence_id]
    with pytest.raises(ValueError, match='human approval'):
        service.execute(pid, pending['plan_id'])
    with pytest.raises(ValueError, match='human approval'):
        service.prepare(pid)
    service.o.run_next_step(pid)
    assert service.o.get_project(pid).phase == ResearchPhase.CRITICAL_REVIEW
    assert any(a.artifact_type == 'execution_analysis' for a in service.o.get_project(pid).artifacts)


def test_repairs_cannot_change_scientific_method(service, monkeypatch):
    service, pid = service
    monkeypatch.setattr(service, 'call', lambda *args: plan())
    pending = service.prepare(pid)
    monkeypatch.setattr(ComputationSandbox, 'execute', lambda *args: {'status': 'failed', 'error': 'NameError'})
    monkeypatch.setattr(service, 'call', lambda *args: plan().model_copy(update={'method': 'different method'}))
    result = service.execute(pid, pending['plan_id'])
    project = service.o.get_project(pid)
    assert result['status'] == 'awaiting_approval'
    assert result['plan_id'] != pending['plan_id']
    assert result['repair_of'] == pending['plan_id']
    assert 'method' in result['repair_changed_fields']
    assert not project.computation['rounds']


@pytest.mark.parametrize('decision,expected_phase', [('iterate', ResearchPhase.EXECUTION_WAITING),
                                                   ('needs_input', ResearchPhase.HUMAN_INTERVENTION_REQUIRED)])
def test_nonfinal_computation_also_passes_through_analysis(service, monkeypatch, decision, expected_phase):
    service, pid = service
    assessment = ComputationAssessment(decision=decision, addresses_question=False,
                                      interpretation='Need additional validation', limitations=['Not closed'],
                                      next_question='Check validation')
    monkeypatch.setattr(service, 'call', lambda *args: plan() if args[3] == ComputationPlan else assessment)
    monkeypatch.setattr(ComputationSandbox, 'execute', lambda *args: {
        'status': 'success', 'result': {'metrics': {'sum_squares': 2870}}, 'artifacts': []})
    pending = service.prepare(pid)
    service.execute(pid, pending['plan_id'])
    assert service.o.get_project(pid).phase == ResearchPhase.DATA_ANALYSIS
    service.o.run_next_step(pid)
    result = service.o.get_project(pid)
    assert result.phase == expected_phase
    assert any(a.artifact_type == 'execution_analysis' for a in result.artifacts)


def test_missing_metrics_does_not_become_evidence(service, monkeypatch):
    service, pid = service
    monkeypatch.setattr(service, 'call', lambda *args: plan())
    monkeypatch.setattr(ComputationSandbox, 'execute', lambda *args: {'status': 'success', 'result': {'metrics': {'wrong': 1}}})
    pending = service.prepare(pid)
    state = service.execute(pid, pending['plan_id'])
    assert state['status'] == 'failed'
    assert len(state['attempts']) == 1
    assert not service.o.get_project(pid).evidence


def test_changed_code_requires_new_approval_even_when_method_metadata_matches(service, monkeypatch):
    service, pid = service
    monkeypatch.setattr(service, 'call', lambda *args: plan())
    pending = service.prepare(pid)
    executions = []
    def failed_execution(*args):
        executions.append(args[1])
        return {'status': 'failed', 'error': 'NameError'}
    monkeypatch.setattr(ComputationSandbox, 'execute', failed_execution)
    monkeypatch.setattr(service, 'call', lambda *args: plan('print("changed scientific behavior")'))
    state = service.execute(pid, pending['plan_id'])
    assert executions == ['pass']
    assert state['status'] == 'awaiting_approval'
    assert state['plan_id'] != pending['plan_id']
    assert state['repair_of'] == pending['plan_id']
    with pytest.raises(ValueError, match='stale'):
        service.execute(pid, pending['plan_id'])


@pytest.mark.skipif(os.getenv('RUN_NAMESPACE_TESTS') != '1', reason='Requires host Linux user namespaces')
def test_real_namespace_calculation_and_isolation(tmp_path):
    code = '''import json, os, socket
from pathlib import Path
assert not Path('/data/ai-scientist/.env').exists()
assert 'DASHSCOPE_API_KEY' not in os.environ
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('network escaped namespace')
json.dump({'metrics': {'sum_squares': sum(x*x for x in range(1,21))}}, open('result.json','w'))
'''
    result = ComputationSandbox(tmp_path).execute(code, [], ['result.json'])
    assert result['status'] == 'success', result
    assert result['result']['metrics']['sum_squares'] == 2870


@pytest.mark.skipif(os.getenv('RUN_NAMESPACE_TESTS') != '1', reason='Requires host Linux user namespaces')
def test_reject_symlink_output(tmp_path):
    code = "import os; os.symlink('/input/missing', 'result.json')"
    result = ComputationSandbox(tmp_path).execute(code, [], ['result.json'])
    assert result['status'] == 'failed'


@pytest.mark.skipif(os.getenv('RUN_NAMESPACE_TESTS') != '1', reason='Requires host Linux user namespaces')
def test_real_scientific_libraries_and_plot(tmp_path):
    code = '''import json
import numpy as np
import scipy.integrate
import sklearn
import matplotlib.pyplot as plt
t = np.linspace(0, 4, 81)
y = scipy.integrate.solve_ivp(lambda t,y: -0.7*y, (0,4), [2.0], t_eval=t, rtol=1e-8).y[0]
error = float(np.max(np.abs(y-2*np.exp(-0.7*t))))
assert error < 1e-5
plt.plot(t,y)
plt.savefig('trajectory.png')
json.dump({'metrics': {'max_error': error}}, open('result.json','w'))
'''
    result = ComputationSandbox(tmp_path).execute(code, [], ['result.json', 'trajectory.png'])
    assert result['status'] == 'success', result
    assert len(result['artifacts']) == 2


@pytest.mark.skipif(os.getenv('RUN_NAMESPACE_TESTS') != '1', reason='Requires host Linux user namespaces')
def test_real_infinite_program_stops(tmp_path):
    result = ComputationSandbox(tmp_path, wall_seconds=1).execute('while True: pass', [], ['result.json'])
    assert result['status'] == 'failed'
    assert 'budget exceeded' in result['error']
    assert result['duration_ms'] < 5000


@pytest.mark.skipif(os.getenv('RUN_NAMESPACE_TESTS') != '1', reason='Requires host Linux user namespaces')
def test_registered_input_is_read_only(tmp_path):
    dataset = tmp_path / 'source.csv'
    dataset.write_text('value\n1\n')
    code = '''import json
try:
    open('/input/source.csv', 'w').write('corrupt')
except OSError:
    blocked = True
else:
    blocked = False
json.dump({'metrics': {'write_blocked': blocked}}, open('result.json','w'))
'''
    result = ComputationSandbox(tmp_path).execute(code, [('source.csv', dataset)], ['result.json'])
    assert result['status'] == 'success', result
    assert result['result']['metrics']['write_blocked']
    assert dataset.read_text() == 'value\n1\n'


def test_frontend_shows_plan_requires_approval_and_renders_attempts(monkeypatch):
    from streamlit.testing.v1 import AppTest

    class Response:
        ok, status_code, text = True, 200, ''
        def __init__(self, payload):
            self.payload = payload
        def json(self):
            return self.payload

    def get(url, **kwargs):
        if url.endswith('/health'):
            return Response({'general_computation_enabled': True})
        return Response([] if url.endswith('/events') else {})

    monkeypatch.setattr('requests.get', get)
    app = AppTest.from_file(Path(__file__).resolve().parents[2] / 'app_streamlit.py')
    app.session_state['research_project_id'] = 'project_test'
    app.session_state['research_project'] = {
        'project_id': 'project_test', 'phase': 'EXECUTION_WAITING', 'planning_only': False,
        'approval_status': 'valid',
        'iteration': 0, 'budget': {}, 'research_assets': [], 'quality_metrics': {},
        'computation': {'status': 'awaiting_approval', 'plan_id': 'plan123', 'plan': plan().model_dump(),
                        'rounds': [], 'attempts': [{'repair_index': 0, 'status': 'failed', 'error': 'test'}]}}
    app.run(timeout=20)
    assert not app.exception
    button = next(b for b in app.button if b.label == '批准并执行计算')
    assert button.disabled
    checkbox = next(c for c in app.checkbox if c.label.startswith('我批准此计算方法'))
    checkbox.check().run(timeout=20)
    assert not app.exception
    assert not next(b for b in app.button if b.label == '批准并执行计算').disabled


def test_async_api_and_active_job_prevent_conflicting_edits(service, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from src import main_api
    from src.ai_scientist.job_store import ResearchJobStore

    service, pid = service
    monkeypatch.setattr(main_api, 'research_orchestrator', service.o)
    monkeypatch.setattr(main_api, 'research_job_store', ResearchJobStore(service.o.store.root))
    monkeypatch.setattr(main_api, 'computation_service', service)
    monkeypatch.setattr(service, 'call', lambda *args: plan())
    with TestClient(main_api.app) as client:
        response = client.post(f'/api/research/{pid}/computation/prepare')
        assert response.status_code == 202
        for _ in range(100):
            job = client.get('/api/research/jobs/' + response.json()['job_id']).json()
            if job['status'] in {'completed', 'failed'}:
                break
            time.sleep(.01)
        assert job['status'] == 'completed', job
        assert service.o.get_project(pid).computation['status'] == 'awaiting_approval'
        main_api.research_job_store.create(pid, 'EXECUTION_WAITING')
        response = client.post(f'/api/research/{pid}/cancel')
        assert response.status_code == 409
