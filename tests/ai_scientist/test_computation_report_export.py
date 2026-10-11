"""Regression for final report export after generated Python computation."""
import json

import pytest

from src.ai_scientist.agents.base_agent import AgentRun
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.report_writer import build_research_plan_markdown
from src.ai_scientist.schemas import Conclusion, ResearchPhase, utc_now
from src.ai_scientist.structured_client import StructuredCallMetadata


def completed_computation(o, rounds=1):
    p = o.create_project('Validate a numerical solution against its analytic reference')
    p.phase = ResearchPhase.SYNTHESIS
    p.planning_only = False
    p.approval_status = 'valid'
    p.internal_execution_summary = {'status': 'complete', 'executor_binding': 'generated_python_v1'}
    p.computation = {'status': 'review_required', 'rounds': [
        {'round': n, 'plan': {'method': 'RK4 numerical validation'},
         'attempts': [{'status': 'success', 'code_sha256': 'fixture-code-hash',
                       'result': {'metrics': {'max_abs_error': 3.94e-12, 'all_passed': True},
                                  'limitations': ['仅为数学模拟']}}],
         'assessment': {'interpretation': 'Compared with the analytic reference.', 'decision': 'stop',
                        'limitations': ['No inference about a physical system.']}}
        for n in range(1, rounds + 1)]}
    o.store.save(p)
    return p


@pytest.mark.parametrize('rounds', [1, 2])
def test_report_renders_generated_computation_results(tmp_path, rounds):
    o = ResearchOrchestrator(tmp_path)
    p = completed_computation(o, rounds)
    report = build_research_plan_markdown(p)
    assert f'Generated computation round {rounds}' in report
    assert 'fixture-code-hash' in report
    assert 'No inference about a physical system.' in report
    assert 'No real experiment, simulation, or data analysis has been executed' not in report
    block = report.split('```json\n', 1)[1].split('\n```', 1)[0]
    assert json.loads(block)['metrics'] == {'max_abs_error': 3.94e-12, 'all_passed': True}
    assert '仅为数学模拟' in block


def test_synthesis_stage_exports_both_reports_and_completes(tmp_path, monkeypatch):
    o = ResearchOrchestrator(tmp_path)
    p = completed_computation(o)
    now = utc_now()
    metadata = StructuredCallMetadata(agent_name='scientific_synthesizer', requested_model='test-double',
        actual_model='test-double', fallback_used=False, started_at=now, finished_at=now,
        model_calls=1, attempted_calls=1, successful_calls=1)
    monkeypatch.setattr(o, '_run_agent', lambda *args: AgentRun(
        output=Conclusion(limitations=['Mathematical simulation only.']), metadata=metadata))
    result = o.run_next_step(p.project_id)
    saved = o.get_project(p.project_id)
    assert result['current_phase'] == 'COMPLETED'
    assert saved.phase == ResearchPhase.COMPLETED
    directory = o.store.project_dir(p.project_id) / 'artifacts'
    assert 'Generated computation round 1' in (directory / 'research_plan.md').read_text()
    exported = json.loads((directory / 'research_plan.json').read_text())
    assert exported['status'] == 'COMPLETED'
    assert exported['computation'] == p.computation
    assert len([a for a in saved.artifacts if a.artifact_type == 'research_plan_synthesis']) == 1
    assert len([a for a in saved.artifacts if a.artifact_type == 'research_plan']) == 2
