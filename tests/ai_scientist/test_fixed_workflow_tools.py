"""Stage/tool integration regressions. Model responses are explicit test doubles."""
from types import SimpleNamespace
import os

import pytest

from src.ai_scientist.agents.evidence_researcher import EvidenceResearcherAgent
from src.ai_scientist.computation import ComputationService, ComputationPlan, ComputationAssessment
from src.ai_scientist.evidence_verifier import verify_evidence_item
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.quality import enrich_evidence_items
from src.ai_scientist.schemas import (
    EvidenceItem, EvidenceResearchOutput, ResearchQuestion, ResearchPhase,
    SearchCandidate, SearchPlan, SearchAcquisitionResult,
)
from src.ai_scientist.tools.search_tools import QwenEvidenceSearchTool


def test_tool_choices_bounded_and_old_plans_default_to_web():
    plan = SearchPlan(queries=['alpha', 'alpha', 'beta'],
                      query_tools={'alpha': 'paper_search', 'deleted': 'paper_search'})
    assert plan.query_tools == {'alpha': 'paper_search', 'beta': 'web_search'}
    assert SearchPlan(queries=['old']).query_tools == {'old': 'web_search'}


def test_direct_reads_are_individual_and_one_failure_does_not_replace_other_pages(monkeypatch):
    def read(url):
        if url.endswith('/blocked'):
            raise ValueError('HTTP 403')
        return {'url': url, 'text': 'Distinct source body ' + url, 'sha256': url[-1],
                'retrieval_status': 'retrieved'}
    monkeypatch.setattr('src.ai_scientist.tools.research_web.read_source', read)
    candidates = [SearchCandidate(url='https://example.org/' + suffix) for suffix in ['a', 'b', 'blocked']]
    result = QwenEvidenceSearchTool().read_sources(candidates)
    reads = [result['source_results'][c.candidate_id] for c in candidates]
    assert result['model_calls'] == 0
    assert reads[0]['text'] != reads[1]['text']
    assert reads[2]['status'] == 'failed' and not reads[2]['text']
    assert reads[0]['snapshot']['retrieved_at']


@pytest.mark.parametrize('quote,read_status,expected', [
    ('Euler has first order global error.', 'completed', True),
    ('RK4 always has zero error.', 'completed', False),
    ('Euler has first order global error.', 'failed', False),
])
def test_actual_quote_verification_overrides_doi_and_model_claims(tmp_path, quote, read_status, expected):
    o = ResearchOrchestrator(tmp_path)
    project = o.create_project('Compare numerical integration error')
    project.question = ResearchQuestion(original_question=project.objective, normalized_question=project.objective)
    source = SearchCandidate(url='https://example.org/euler', extraction_status=read_status,
                             extracted_text='In this setting Euler has first order global error.',
                             retrieval_snapshot={'sha256': 'actual-snapshot-hash', 'retrieval_status': 'retrieved'})
    item = EvidenceItem(title='Euler', summary='Numerical error', source_url=source.url,
                        doi='10.1000/example', source_quote=quote, verified=True,
                        retrieval_provenance={'sha256': 'model-invented', 'quote_matched': True})
    client = SimpleNamespace(call=lambda *a: SimpleNamespace(
        value=EvidenceResearchOutput(evidence=[item]), metadata=None))
    agent = EvidenceResearcherAgent(client=client)
    output = agent.normalize_search_result(project, SearchAcquisitionResult(selected_candidates=[source]))
    verified = enrich_evidence_items(output.output.evidence)[0]
    assert verified.verified is expected
    assert verified.retrieval_provenance['sha256'] == 'actual-snapshot-hash'
    assert verify_evidence_item(verified).verified is expected  # re-grading must not undo the check


def _plan():
    return ComputationPlan(decision='execute', rationale='Explicit synthetic calculation', method='sum squares',
                           assumptions=[], input_files=[], expected_outputs=['result.json'],
                           required_metrics=['sum_squares'], validation_checks=['exact integer sum'],
                           stopping_rule='one round', code='pass')


def test_original_step_prepares_but_never_executes_without_code_approval(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_SCIENTIST_ENABLE_COMPUTATION', '1')
    monkeypatch.setattr('src.ai_scientist.computation.runtime_status', lambda: {'available': True})
    calls = []
    monkeypatch.setattr(ComputationService, 'call', lambda *a: calls.append('plan') or _plan())
    o = ResearchOrchestrator(tmp_path)
    p = o.create_project('Sum squares 1..20', planning_only=False)
    p.phase, p.approval_status = ResearchPhase.EXECUTION_WAITING, 'valid'
    o.store.save(p)
    first = o.run_next_step(p.project_id)
    assert first['stage_status'] == 'computation_awaiting_approval'
    second = o.run_next_step(p.project_id)
    assert second['current_phase'] == 'EXECUTION_WAITING'
    assert calls == ['plan']
    assert not o.get_project(p.project_id).evidence
    assert not o.get_project(p.project_id).computation.get('attempts')


@pytest.mark.parametrize('phase', ['INTAKE', 'DATA_ANALYSIS', 'CRITICAL_REVIEW', 'SYNTHESIS', 'COMPLETED'])
def test_computation_cannot_bypass_fixed_workflow(tmp_path, monkeypatch, phase):
    monkeypatch.setenv('AI_SCIENTIST_ENABLE_COMPUTATION', '1')
    o = ResearchOrchestrator(tmp_path)
    p = o.create_project('An arbitrary question', planning_only=False)
    p.phase, p.approval_status = ResearchPhase(phase), 'valid'
    with pytest.raises(ValueError, match='human approval'):
        ComputationService(o).check(p)


def test_computation_requires_valid_research_approval(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_SCIENTIST_ENABLE_COMPUTATION', '1')
    o = ResearchOrchestrator(tmp_path)
    p = o.create_project('An arbitrary question', planning_only=False)
    p.phase = ResearchPhase.EXECUTION_WAITING
    with pytest.raises(ValueError, match='human approval'):
        ComputationService(o).check(p)


@pytest.mark.skipif(os.getenv('RUN_NAMESPACE_TESTS') != '1', reason='Requires host Linux namespaces')
def test_real_sandbox_rejoins_original_analysis_and_scientific_review(tmp_path, monkeypatch):
    from src.ai_scientist.agents.skeptical_reviewer import SkepticalReviewerAgent
    monkeypatch.setenv('AI_SCIENTIST_ENABLE_COMPUTATION', '1')
    executable = _plan().model_copy(update={'code':
        "import json\njson.dump({'metrics': {'sum_squares': sum(i*i for i in range(1,21))}}, open('result.json','w'))"})
    assessment = ComputationAssessment(decision='stop', addresses_question=True,
                                       interpretation='Actual exact sum recorded', limitations=[], next_question='')
    monkeypatch.setattr(ComputationService, 'call', lambda self, p, instructions, payload, schema, kind:
                        executable if schema == ComputationPlan else assessment)
    o = ResearchOrchestrator(tmp_path)
    p = o.create_project('Calculate exact sum of squares 1..20', planning_only=False)
    p.phase, p.approval_status = ResearchPhase.EXECUTION_WAITING, 'valid'
    o.store.save(p)
    o.run_next_step(p.project_id)
    pending = o.get_project(p.project_id).computation
    ComputationService(o).execute(p.project_id, pending['plan_id'])  # explicit approval in test fixture
    computed = o.get_project(p.project_id)
    assert computed.phase == ResearchPhase.DATA_ANALYSIS
    assert computed.computation['attempts'][-1]['result']['metrics']['sum_squares'] == 2870
    o.run_next_step(p.project_id)
    reviewed = o.get_project(p.project_id)
    assert reviewed.phase == ResearchPhase.CRITICAL_REVIEW
    assert SkepticalReviewerAgent().build_payload(reviewed)['review_scope']['type'] == 'execution_result_review'
    assert reviewed.conclusion is None


def test_fixed_frontend_archives_parallel_workspace_without_hiding_workflow(monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    class Response:
        ok, status_code, text = True, 200, ''
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload
    monkeypatch.setattr('requests.get', lambda url, **kwargs: Response(
        {'general_computation_enabled': True} if url.endswith('/health') else
        [] if url.endswith('/events') else {}))
    app = AppTest.from_file(Path(__file__).resolve().parents[2] / 'app_streamlit.py')
    app.session_state['research_project_id'] = 'project_archive_test'
    app.session_state['research_project'] = {
        'project_id': 'project_archive_test', 'objective': '研究步长如何影响数值误差',
        'phase': 'INTAKE', 'planning_only': False, 'budget': {},
        'research_workspace': {'status': 'awaiting_approval', 'pending_computation': {'plan_id': 'old'}},
    }
    app.run(timeout=20)
    assert not app.exception
    assert any(h.value == '当前进展与下一步' for h in app.subheader)
    assert any(b.label == '开始研究' for b in app.button)
    assert any('研究步长如何影响数值误差' in m.value for m in app.markdown)
    assert not any(b.label == '批准此版本并执行' for b in app.button)
    assert any(e.label == '历史自主工作台记录（只读）' for e in app.expander)
