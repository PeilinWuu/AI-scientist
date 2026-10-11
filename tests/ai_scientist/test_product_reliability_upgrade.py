"""Offline regressions. No model calls or real research mutations."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from src.ai_scientist.document_parsers import parse_research_asset
from src.ai_scientist.structured_client import StructuredQwenClient, _call_context, StructuredCallMetadata
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import ResearchPhase, utc_now
from src.ai_scientist.product_ui import latest_successful_attempt
from src.ai_scientist.delivery_report import generate_report, ReportDraft
from src.ai_scientist.background_agent import BackgroundAgent, BackgroundAction


def test_budget_explicit_and_override_preserved(monkeypatch):
    monkeypatch.setenv('AI_SCIENTIST_THINKING_BUDGET', '1234')
    client = StructuredQwenClient()
    token = _call_context.set({'agent_name': 'analyst'})
    try:
        assert client._bounded_options('qwen3.8-max')['extra_body'] == {
            'enable_thinking': True, 'thinking_budget': 1234}
        client.request_options = {'extra_body': {'enable_thinking': False}}
        assert client._bounded_options('qwen3.8-max')['extra_body'] == {'enable_thinking': False}
        client.request_options = {}
        assert 'extra_body' not in client._bounded_options('other-provider-model')
        _call_context.set({'agent_name': 'domain_analyst'})
        assert not client._bounded_options('qwen3.8-max')['extra_body']['enable_thinking']
    finally:
        _call_context.reset(token)


@pytest.mark.parametrize('text,columns,rows', [
    ('# source metadata\n# units ppm\nyear,mean,unc\n2000,370,1\n2001,372,1\n', ['year', 'mean', 'unc'], 2),
    ('Land-Ocean: Global Means\nYear,Jan,J-D\n2000,0.2,0.3\n2001,0.3,0.4\n', ['Year', 'Jan', 'J-D'], 2),
])
def test_scientific_csv_header_keeps_original_bytes(tmp_path, text, columns, rows):
    path = tmp_path / 'data.csv'
    path.write_text(text)
    parsed = parse_research_asset(path)
    assert parsed.structured_summary['column_names'] == columns
    assert parsed.structured_summary['scanned_data_rows'] == rows
    assert parsed.warnings
    assert path.read_text() == text


def test_project_names_and_recency(tmp_path):
    o = ResearchOrchestrator(tmp_path)
    p = o.create_project('A real environmental research question')
    p.title = '空气质量研究'
    o.store.save(p)
    records = o.store.list_projects()
    assert records[0]['title'] == '空气质量研究'
    assert set(records[0]) == {'title', 'project_id', 'phase', 'updated_at'}


def test_gallery_can_show_success_before_assessment():
    assert latest_successful_attempt({'computation': {'attempts': [
        {'status': 'failed'}, {'status': 'success', 'run_id': 'new'}], 'rounds': []}})['run_id'] == 'new'


def test_download_requires_authorization_and_observed_url(tmp_path, monkeypatch):
    o = ResearchOrchestrator(tmp_path)
    p = o.create_project('Compare environmental observations')
    url = 'https://example.org/observations.csv'
    p.background_agent = {'sources': [{'url': url, 'candidate_id': 's1'}],
                          'authorization': {'allow_network': False}}
    o.store.save(p)
    agent = BackgroundAgent(o)
    action = BackgroundAction(action='download_data', purpose='Obtain the observed data file', url=url)
    with pytest.raises(ValueError, match='not been authorized'):
        agent.perform(p, action)
    p.background_agent['authorization']['allow_network'] = True
    with pytest.raises(ValueError, match='exact dataset link'):
        agent.perform(p, action.model_copy(update={'url': 'https://example.org/guessed.csv'}))
    monkeypatch.setattr('src.ai_scientist.tools.research_web.fetch_public',
                        lambda url: (url, 'text/csv', b'year,value\n2000,1\n2001,2\n'))
    result = agent.perform(p, action)
    assert result['status'] == 'registered'
    assert p.research_assets[-1].source == url
    assert p.research_assets[-1].purpose == 'data'
    read = agent.perform(p, BackgroundAction(action='inspect_asset', purpose='Read original dataset',
                                             asset_id=result['asset_id']))
    assert read['content_origin'] == 'registered_raw_file'
    assert read['text'].startswith('year,value\n')


def test_report_is_real_file_from_verified_inputs_and_no_execution(tmp_path, monkeypatch):
    o = ResearchOrchestrator(tmp_path)
    p = o.create_project('环境观测研究', planning_only=False)
    p.phase = ResearchPhase.CRITICAL_REVIEW
    root = o.store.project_dir(p.project_id)
    run = root / 'computation' / 'run1'
    (run / 'output').mkdir(parents=True)
    program = run / 'program.py'
    program.write_text('# actual executed source')
    output = run / 'output' / 'result.json'
    output.write_text('{"metrics":{"observed_mean":2.5}}')
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    audit = {'run_id': 'run1', 'status': 'success', 'result': {'metrics': {'observed_mean': 2.5}},
             'code_path': str(program.relative_to(root)), 'code_sha256': sha(program),
             'artifacts': [{'relative_path': str(output.relative_to(root)), 'checksum_sha256': sha(output),
                            'size_bytes': output.stat().st_size}]}
    p.computation = {'rounds': [{'attempts': [audit], 'plan': {'expected_outputs': ['result.json']}}]}
    captured = []
    text = '# 环境观测报告\n\n' + '观测均值为 2.5，仅描述现有样本，不能由此建立因果结论。' * 5
    def call(role, instructions, payload, schema):
        captured.append(payload)
        return SimpleNamespace(value=ReportDraft(markdown=text), metadata=StructuredCallMetadata(
            agent_name=role, requested_model='offline', actual_model='offline', fallback_used=False,
            started_at=utc_now(), finished_at=utc_now(), model_calls=1))
    monkeypatch.setattr(o, '_structured_client_for_project', lambda p: SimpleNamespace(call=call))
    artifact_id = generate_report(o, p)
    report = p.computation['delivery_report']
    assert report['artifact_id'] == artifact_id
    assert (root / 'artifacts' / report['filename']).read_text() == text
    assert captured[0]['actual_artifacts']['delivery_validation']['status'] == 'complete'
    assert p.phase == ResearchPhase.CRITICAL_REVIEW
    assert len(p.computation['rounds']) == 1
    output.write_text('tampered')
    with pytest.raises(ValueError, match='missing or modified'):
        generate_report(o, p)
    assert len(captured) == 1
