"""Network/model doubles check reader mechanics, not scientific relevance."""

from types import SimpleNamespace

import pytest

from src.ai_scientist.background_agent import BackgroundAction, BackgroundAgent, SourceAssessmentReview
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import ResearchPhase, SearchCandidate, utc_now
from src.ai_scientist.structured_client import StructuredCallMetadata, StructuredCallResult
from src.ai_scientist.tools import research_web


@pytest.fixture
def stage(tmp_path):
    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project('比较两种统计方法；未经执行不能宣称验证成功', research_policy_version=2)
    project.phase = ResearchPhase.BACKGROUND_RESEARCH
    candidate = SearchCandidate(title='A methods paper', url='https://example.org/paper', query='methods', rank=1)
    project.background_agent = {
        'sources': [candidate.model_dump(mode='json')], 'assessments': {}, 'observations': [],
        'authorization': {'allow_network': True},
    }
    return BackgroundAgent(orchestrator), project, candidate.candidate_id


def action(name, **kwargs):
    return BackgroundAction(action=name, purpose='根据实际工具返回继续读取来源', **kwargs)


def cached_snapshot(text, **overrides):
    return {'url': 'https://example.org/paper', 'title': 'A methods paper', 'text': text,
            'sha256': 'test-snapshot-hash', 'truncated': False, **overrides}


def test_short_alias_resolves_to_canonical_evidence_id(stage, monkeypatch):
    service, project, source_id = stage
    monkeypatch.setattr(service, 'call', lambda *args, **kwargs:
        SourceAssessmentReview(supported=True, rationale='Reader mechanics test double'))
    text = 'This source discusses uncertainty in confidence interval coverage.'
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', lambda url: cached_snapshot(text))
    read = service.perform(project, action('read_source', source_id='S1'))
    assert read['source_id'] == source_id
    assert read['source_alias'] == 'S1'
    assessment = service.perform(project, action('assess_source', source_id='s1', decision='keep',
        quote=text, summary='来源中的区间覆盖率讨论', relevance='可作为方法背景', credibility='仍需要判断适用范围'))
    assert assessment['source_id'] == source_id
    assert list(project.background_agent['assessments']) == [source_id]
    assert assessment['quote_matched'] is True
    assert 'scientific_truth_verified' not in assessment


def test_unknown_alias_reports_available_choices(stage):
    service, project, _ = stage
    with pytest.raises(ValueError, match=r'Available aliases: S1 \(A methods paper\)'):
        service.perform(project, action('read_source', source_id='S9'))


def test_paging_goes_beyond_old_cache_without_sending_full_snapshot(stage, monkeypatch):
    service, project, _ = stage
    text = ('A long source about the methods and their limitations. ' * 1000)[:50000]
    calls = []
    def fetch(url):
        calls.append(url)
        return cached_snapshot(text, source_text_chars=len(text), cache_truncated=False)
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', fetch)
    pages = []
    offset = 0
    while True:
        page = service.perform(project, action('read_source', source_id='S1', offset=offset))
        pages.append(page)
        assert 0 < len(page['text']) <= 12000
        if page['next_offset'] is None:
            break
        assert not page['eof']
        offset = page['next_offset']
    assert len(calls) == 1
    assert ''.join(page['text'] for page in pages) == text
    assert pages[-1]['eof'] is True
    assert pages[-1]['read_ranges'] == [{'start': 0, 'end': len(text)}]
    assert pages[-1]['cache_truncated'] is False
    assert pages[-1]['offset'] >= 24000
    with pytest.raises(ValueError, match='No source text was read'):
        service.perform(project, action('read_source', source_id='S1', offset=len(text)))


def test_out_of_order_pages_record_only_actual_ranges(stage, monkeypatch):
    service, project, _ = stage
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', lambda url: cached_snapshot('x' * 50000))
    late = service.perform(project, action('read_source', source_id='S1', offset=24000))
    assert late['read_ranges'] == [{'start': 24000, 'end': 36000}]
    first = service.perform(project, action('read_source', source_id='S1'))
    assert first['read_ranges'] == [{'start': 0, 'end': 12000}, {'start': 24000, 'end': 36000}]
    overlap = service.perform(project, action('read_source', source_id='S1', offset=10000))
    assert overlap['read_ranges'] == [{'start': 0, 'end': 22000}, {'start': 24000, 'end': 36000}]


def test_legacy_truncated_cache_cannot_claim_full_text_or_empty_success(stage, monkeypatch):
    service, project, _ = stage
    source = project.background_agent['sources'][0]
    source.update(extracted_text='x' * 24000,
                  retrieval_snapshot=cached_snapshot('x' * 24000, truncated=True))
    def must_not_fetch(url):
        raise AssertionError('Existing cache must not be silently replaced')
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', must_not_fetch)
    page = service.perform(project, action('read_source', source_id='S1', offset=12000))
    assert page['cache_truncated'] is True
    assert page['cache_eof'] is True
    assert page['eof'] is False
    assert page['next_offset'] is None
    with pytest.raises(ValueError, match='cache is truncated'):
        service.perform(project, action('read_source', source_id='S1', offset=24000))


def test_source_keep_errors_identify_the_missing_requirement(stage, monkeypatch):
    service, project, _ = stage
    text = 'An exact contiguous quotation which describes the tested statistical method.'
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', lambda url: cached_snapshot(text))
    request = action('assess_source', source_id='S1', decision='keep', quote=text,
                     summary='摘要', relevance='与问题相关', credibility='限定可信度')
    with pytest.raises(ValueError, match='no actual read'):
        service.perform(project, request)
    service.perform(project, action('read_source', source_id='S1'))
    with pytest.raises(ValueError, match='quote is too short'):
        service.perform(project, request.model_copy(update={'quote': 'short'}))
    with pytest.raises(ValueError, match='one contiguous excerpt'):
        service.perform(project, request.model_copy(update={'quote': 'An exact contiguous ... statistical method.'}))
    with pytest.raises(ValueError, match='missing credibility assessment'):
        service.perform(project, request.model_copy(update={'credibility': '   '}))


def test_failed_download_records_failure_not_completed_extraction(stage, monkeypatch):
    service, project, _ = stage
    def fail(url):
        raise ValueError('read_failed: CAPTCHA access challenge')
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', fail)
    with pytest.raises(ValueError, match='CAPTCHA'):
        service.perform(project, action('read_source', source_id='S1'))
    source = project.background_agent['sources'][0]
    assert source['extraction_status'] == 'failed'
    assert source['retrieval_status'] == 'read_failed'
    assert not source.get('shown_excerpts')
    assert 'CAPTCHA' in source['extraction_error']


def test_old_cached_access_challenge_is_not_reused_as_paper_text(stage):
    service, project, _ = stage
    text = 'Please verify that you are human. Incapsula incident ID 123456.'
    source = project.background_agent['sources'][0]
    source.update(extracted_text=text, retrieval_snapshot=cached_snapshot(text, title='Security check'))
    with pytest.raises(ValueError, match='not the requested source text'):
        service.perform(project, action('read_source', source_id='S1'))
    assert source['retrieval_status'] == 'read_failed'
    assert not source.get('shown_excerpts')


def test_old_quote_from_captcha_cannot_be_recommended_as_paper(stage):
    service, project, _ = stage
    text = 'Please verify that you are human. Incapsula incident ID 123456.'
    source = project.background_agent['sources'][0]
    source.update(extracted_text=text, shown_excerpts=[text],
                  retrieval_snapshot=cached_snapshot(text, title='Security check'))
    with pytest.raises(ValueError, match='no actual read'):
        service.perform(project, action('assess_source', source_id='S1', decision='keep', quote=text,
            summary='错误地将旧缓存当正文', relevance='需要原文', credibility='尚不可判断'))
    assert source['retrieval_status'] == 'read_failed'


@pytest.mark.parametrize('title,body', [
    ('Just a moment...', 'Enable JavaScript and cookies to continue. ' * 3),
    ('Security check', 'Please verify that you are human before viewing this page. ' * 3),
    ('reCAPTCHA', 'This is a browser challenge; automated access is not allowed. ' * 3),
    ('Site access', 'Incapsula incident ID: 123456. Your request cannot be completed. ' * 3),
])
def test_public_reader_rejects_access_interstitials(monkeypatch, title, body):
    html = f'<title>{title}</title><main>{body}</main>'.encode()
    monkeypatch.setattr(research_web, 'fetch_public', lambda url: (url, 'text/html', html))
    with pytest.raises(ValueError, match='read_failed'):
        research_web.read_source_snapshot('https://example.org/paper')


def test_captcha_research_topic_is_not_filtered_as_irrelevant(monkeypatch):
    html = b'<title>A comparison of CAPTCHA designs</title><p>This research discusses CAPTCHA usability and measurements from a controlled study; it is not a live access challenge.</p>'
    monkeypatch.setattr(research_web, 'fetch_public', lambda url: (url, 'text/html', html))
    result = research_web.read_source_snapshot('https://example.org/paper')
    assert result['retrieval_status'] == 'retrieved'
    assert 'CAPTCHA usability' in result['text']


def test_full_cache_is_bounded_and_legacy_context_does_not_expand(monkeypatch):
    body = ('Methods, limitations and experimental evidence. ' * 6000).encode()
    monkeypatch.setattr(research_web, 'fetch_public', lambda url: (url, 'text/plain', body))
    snapshot = research_web.read_source_snapshot('https://example.org/paper')
    assert len(snapshot['text']) == 200000
    assert snapshot['cache_truncated'] is True
    assert snapshot['source_text_chars'] > 200000
    assert snapshot['cache_limit_chars'] == 200000
    legacy = research_web.read_source('https://example.org/paper')
    assert len(legacy['text']) == 24000
    assert legacy['truncated'] is True


def test_background_model_receives_shared_memory_and_short_source_aliases(stage, monkeypatch):
    service, project, _ = stage
    project.revision_feedback.append('新的要求：如实说明未执行的工作。')
    captured = []

    class FakeClient:
        def __init__(self, registry):
            self.registry = registry
            self.http_client = None
            self.provider_responses = []
        def _get_client(self):
            return SimpleNamespace(with_options=lambda **kwargs: object())
        def call(self, role, instructions, payload, schema):
            captured.append((instructions, payload))
            now = utc_now()
            return StructuredCallResult(action('ask_user', summary='需要补充研究数据。'), StructuredCallMetadata(
                agent_name=role, requested_model='test', actual_model='test', fallback_used=False,
                started_at=now, finished_at=now, model_calls=1, attempted_calls=1, successful_calls=1))

    monkeypatch.setattr('src.ai_scientist.background_agent.StructuredQwenClient', FakeClient)
    service.call(project, BackgroundAction, {'sources': [dict(project.background_agent['sources'][0])]})
    instructions, payload = captured[0]
    assert 'shared_research_context' in instructions
    assert payload['sources'][0]['source_alias'] == 'S1'
    assert payload['shared_research_context']['human_feedback']['revision_feedback'][0].startswith('新的要求')
    assert 'extracted_text' not in payload['shared_research_context']['research_basis']
