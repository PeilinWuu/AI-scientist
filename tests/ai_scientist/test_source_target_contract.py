"""Regression for the real office-plants failure; network doubles are explicit."""
import pytest

from src.ai_scientist.background_agent import BackgroundAgent, BackgroundAction
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import SearchCandidate
from src.ai_scientist.tools.search_tools import enrich_source_previews
from src.ai_scientist.tool_presentation import tool_view
from src.ai_scientist.markdown_links import readable_links


@pytest.fixture
def stage(tmp_path, monkeypatch):
    o = ResearchOrchestrator(tmp_path)
    p = o.create_project('办公室盆栽能否净化空气？', research_policy_version=2)
    c = SearchCandidate(title='News', url='https://example.org/news', query='plants', rank=1).model_dump(mode='json')
    c.update(retrieval_snapshot={'url': c['url'], 'title': 'News', 'sha256': 'news',
                                'links': ['https://example.org/paper'], 'text': 'news ' * 3000},
             extracted_text='news ' * 3000)
    p.background_agent = {'sources': [c], 'observations': [], 'assessments': {},
                          'authorization': {'allow_network': True}}
    calls = []
    def fetch(url):
        calls.append(url)
        return {'url': url, 'title': 'Paper', 'sha256': 'paper', 'text': 'paper text ' * 500, 'links': []}
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', fetch)
    return BackgroundAgent(o), p, calls


def action(**kw):
    return BackgroundAction(action='read_source', **kw)


def test_conflict_cannot_silently_read_cached_news(stage):
    s, p, calls = stage
    with pytest.raises(ValueError, match='Source target mismatch'):
        s.perform(p, action(source_id='S1', url='https://example.org/paper'))
    assert not calls
    assert not p.background_agent['sources'][0].get('shown_excerpts')


def test_follow_observed_link_has_own_identity_and_snapshot(stage):
    s, p, calls = stage
    result = s.perform(p, action(url='https://example.org/paper'))
    assert calls == ['https://example.org/paper']
    assert result['source_alias'] == 'S2'
    assert result['url'] == 'https://example.org/paper'
    assert p.background_agent['sources'][1]['discovered_from'] == p.background_agent['sources'][0]['candidate_id']
    assert not p.evidence
    assert s.perform(p, action(source_id='S2'))['new_chars'] == 0
    assert len(calls) == 1


def test_unknown_link_or_network_denial_never_fetches(stage):
    s, p, calls = stage
    with pytest.raises(ValueError, match='observed HTTPS'):
        s.perform(p, action(url='https://invented.example/paper'))
    p.background_agent['authorization']['allow_network'] = False
    with pytest.raises(ValueError, match='not been authorized'):
        s.perform(p, action(url='https://example.org/paper'))
    assert not calls


def test_new_read_ranges_not_io_success_measure_progress(stage):
    s, p, _ = stage
    assert s.perform(p, action(source_id='S1'))['new_chars'] == 12000
    assert s.perform(p, action(source_id='S1'))['new_chars'] == 0
    assert s.perform(p, action(source_id='S1', offset=10000))['new_chars'] == 3000


def test_preview_is_actual_excerpt_not_generated_abstract(monkeypatch):
    c = SearchCandidate(title='', url='https://example.org/paper', query='plants', rank=1)
    monkeypatch.setattr('src.ai_scientist.tools.research_web.read_source_snapshot',
                        lambda url: {'url': url, 'title': 'Actual title', 'text': 'actual text '*300, 'sha256': 'x'})
    previews = enrich_source_previews([c])
    assert c.title == 'Actual title'
    assert c.snippet == previews[c.url]['text'][:1200]
    assert '非论文摘要' in c.verification_note
    assert not c.ai_summary


def test_preview_failure_keeps_link_without_fake_content(monkeypatch):
    c = SearchCandidate(title='', url='https://example.org/paper', query='plants', rank=1)
    def denied(url):
        raise ValueError('403')
    monkeypatch.setattr('src.ai_scientist.tools.research_web.read_source_snapshot', denied)
    assert enrich_source_previews([c]) == {}
    assert not c.snippet and '不可用' in c.verification_note


def test_tool_labels_are_outcomes_not_intentions():
    o = {'action': 'read_source', 'purpose': 'Read Nature paper', 'status': 'completed',
         'result': {'status': 'read_excerpt', 'url': 'https://drexel.edu/news'}}
    assert tool_view(o) == '工具已返回 · 读取网页 · drexel.edu'
    o.update(status='running', job_id='job1')
    assert '正在执行' in tool_view(o, 'job1')
    assert '中断' in tool_view(o)
    o.update(status='completed', result={'status': 'phrase_not_found'})
    assert '未取得' in tool_view(o)
    o['result'] = {'status': 'read_excerpt', 'new_chars': 0}
    assert '没有新增' in tool_view(o)


def test_bare_url_boundary_preserves_prose_explicit_links_and_code():
    text = '参阅（https://www.nature.com/articles/example）以获取数据。'
    assert readable_links(text) == '参阅（<https://www.nature.com/articles/example>）以获取数据。'
    explicit = '[中文](https://example.org/中文) `https://example.org/code`\n```\nhttps://x.test/a\n```'
    assert readable_links(explicit) == explicit
    assert readable_links(readable_links(text)) == readable_links(text)


def test_follow_link_still_passes_through_public_network_guard(stage, monkeypatch):
    from src.ai_scientist.tools.research_web import read_source_snapshot
    s, p, _ = stage
    p.background_agent['sources'][0]['retrieval_snapshot']['links'].append('https://127.0.0.1/private')
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', read_source_snapshot)
    with pytest.raises(ValueError, match='Private, loopback'):
        s.perform(p, action(url='https://127.0.0.1/private'))
    assert p.background_agent['sources'][-1]['retrieval_status'] == 'read_failed'
    assert not p.background_agent['sources'][-1].get('shown_excerpts')


def test_preview_cache_is_reused_but_does_not_count_as_model_reading(stage, monkeypatch):
    from src.ai_scientist.tools.search_tools import QwenEvidenceSearchTool
    s, p, calls = stage
    url = 'https://example.org/new'
    raw = SearchCandidate(title='New', url=url, query='plants', rank=1).model_dump(mode='json')
    snapshot = {'url': url, 'text': 'source passage '*100, 'title': 'New', 'sha256': 'new'}
    monkeypatch.setattr(QwenEvidenceSearchTool, 'search_query', lambda *args: {
        'search_executed': True, 'candidates': [raw], 'source_previews': {url: snapshot}})
    s.perform(p, BackgroundAction(action='search_web', query='plants'))
    c = p.background_agent['sources'][-1]
    assert not c.get('shown_excerpts') and not c.get('read_ranges')
    assert s.perform(p, action(source_id=c['source_alias']))['text'] == snapshot['text']
    assert not calls


def test_continuation_does_not_count_duplicate_ranges_as_progress(stage):
    from src.ai_scientist.continuous_research import progress_signature
    s, p, _ = stage
    before = progress_signature(p)
    p.background_agent['observations'].append({'status': 'completed', 'result': {'status': 'read_excerpt', 'new_chars': 0}})
    assert progress_signature(p) == before
def test_reading_draft_is_not_duplicated_or_presented_as_final():
    from src.ai_scientist.research_timeline import reading_draft_state
    project = {'background_agent': {'partial_answer': {'markdown': 'Draft answer'},
               'proposal_review': {'decision': 'revise', 'rationale': 'Missing evidence',
                                   'unsupported_completion_claims': ['Not all sources read']}}}
    state = reading_draft_state(project, [{'public_response': 'Draft answer\n'}])
    assert state['already_in_timeline'] and state['needs_revision']
    assert '仍需修订' in state['label']
    assert state['review_note'] == 'Missing evidence'
    project['background_agent'].pop('proposal_review')
    state = reading_draft_state(project, [])
    assert not state['already_in_timeline'] and not state['needs_revision']
    assert '尚非最终' in state['label']
