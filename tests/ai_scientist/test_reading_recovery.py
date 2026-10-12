"""Regression doubles for the road-greening user test; not live research evidence."""
import io

import pytest

from src.ai_scientist.background_agent import BackgroundAction, ReadingNotes, BasisProposalReview, SourceAssessmentReview
from src.ai_scientist.background_memory import action_payload
from src.ai_scientist.tools import research_web
from src.ai_scientist.tools.search_tools import _search_candidates
from tests.ai_scientist.test_background_agent import stage, authorize, source, action


def test_notes_do_not_require_redundant_internal_purpose():
    result = ReadingNotes.model_validate({'answer': '有限回答', 'assessments': [
        {'action': 'assess_source', 'source_id': 'S1', 'decision': 'uncertain', 'summary': '材料有限'}]})
    assert result.assessments[0].purpose


def test_single_object_envelope_does_not_require_paid_repair():
    import json
    from src.ai_scientist.structured_client import json_document
    assert json.loads(json_document('[{"summary":"kept verbatim"}]')) == {'summary': 'kept verbatim'}
    assert isinstance(json.loads(json_document('[{"a":1},{"a":2}]')), list)


def test_quote_typography_is_not_a_scientific_mismatch(stage, monkeypatch):
    agent, project = stage
    authorize(agent, project)
    sid = source(project)
    text = 'Vegetation’s impacts depend on local conditions, with no universal benefit.'
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', lambda url:
        {'url': url, 'text': text, 'sha256': 'fixture', 'truncated': False})
    agent.perform(project, action('read_source', source_id=sid))
    result = agent.perform(project, action('assess_source', source_id=sid, decision='keep',
        quote=text.replace('’', "'"), summary='情境依赖', relevance='与问题相关', credibility='实际阅读'))
    assert result['quote_matched']


def test_reading_only_failure_never_resumes_search(stage, monkeypatch):
    agent, project = stage
    authorize(agent, project)
    agent.feedback(project, '只整理已有资料', consolidate=True)
    calls = []
    def perform(p, request):
        calls.append(request.action)
        raise ValueError('Fixture: consolidation unavailable')
    monkeypatch.setattr(agent, 'perform', perform)
    monkeypatch.setattr(agent, 'call', lambda *a, **kw: pytest.fail('Must not ask the actor to resume search'))
    agent.advance(project)
    assert calls == ['finish']
    assert project.background_agent['status'] == 'needs_input'


def test_explicit_consolidation_needs_no_new_network_action_quota(stage, monkeypatch):
    agent, project = stage
    authorize(agent, project)
    project.background_agent['authorization']['action_limit'] = 0
    agent.feedback(project, '停止联网，只整理资料', consolidate=True)
    calls = []
    def perform(p, request):
        calls.append(request.action)
        p.background_agent['status'] = 'needs_input'
        return {'status': 'partial_answer_ready'}
    monkeypatch.setattr(agent, 'perform', perform)
    agent.advance(project)
    assert calls == ['finish']


def test_optional_bad_reference_does_not_block_valid_action(stage, monkeypatch):
    agent, project = stage
    authorize(agent, project)
    monkeypatch.setattr(agent, 'call', lambda *a, **kw: action('ask_user',
        summary='请提供研究数据。', observation_ids=['misspelled-artifact']))
    agent.advance(project)
    observation = project.background_agent['observations'][0]
    assert observation['status'] == 'completed'
    assert observation['observation_ids'] == []
    assert observation['unresolved_optional_references'] == ['misspelled-artifact']
    assert not project.evidence


def test_failed_url_is_not_requested_again_without_reason(stage, monkeypatch):
    agent, project = stage
    authorize(agent, project)
    sid = source(project)
    calls = []
    def fetch(url):
        calls.append(url)
        raise ValueError('Public source returned HTTP 403')
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', fetch)
    with pytest.raises(ValueError, match='403'):
        agent.perform(project, action('read_source', source_id=sid))
    with pytest.raises(ValueError, match='previously failed'):
        agent.perform(project, action('read_source', source_id=sid))
    assert len(calls) == 1
    assert action_payload(project)['failed_hosts'] == ['example.org']
    with pytest.raises(ValueError, match='403'):
        agent.perform(project, action('read_source', source_id=sid, retry_reason='Source owner reports outage resolved'))
    assert len(calls) == 2


def test_finish_consolidates_reading_without_separate_bookkeeping(stage, monkeypatch):
    agent, project = stage
    authorize(agent, project)
    sid = source(project)
    quote = 'Tree effects depend on ventilation and deposition under the studied conditions.'
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', lambda url:
        {'url': url, 'text': quote, 'sha256': 'fixture', 'truncated': False})
    agent.perform(project, action('read_source', source_id=sid))
    def call(p, schema, payload, **kwargs):
        if schema == ReadingNotes:
            assert quote in payload['actual_read_passages'][0]['passages'][0]['text']
            return ReadingNotes(answer='有限的阶段回答，不代表完整综述。', assessments=[action('assess_source',
                source_id='S1', decision='keep', passage_id='S1:P1', summary='情境依赖', relevance='支持讨论机制', credibility='有限的已读资料')])
        if schema == SourceAssessmentReview:
            return SourceAssessmentReview(supported=True, rationale='Fixture supports the limited claim')
        return BasisProposalReview(decision='ready_for_human', rationale='Only proposes a background basis')
    monkeypatch.setattr(agent, 'call', call)
    agent.perform(project, action('finish', summary='据实际阅读提出有限研究依据'))
    assert project.background_agent['status'] == 'awaiting_adoption'
    assert project.background_agent['assessments'][sid]['quote_matched']
    assert project.background_agent['partial_answer']['status'] == 'reading_synthesis_not_final'
    assert not project.evidence  # No automatic human approval.
    assert project.background_agent['assessments'][sid]['semantic_review'] == {'status': 'not_requested'}
    from tests.ai_scientist.test_background_agent import adopt
    adopt(agent, project, [sid])
    assert project.evidence[0].retrieval_provenance['semantic_review_status'] == 'not_requested'
    assert any('尚未独立核查' in text for text in project.evidence[0].limitations)


def test_failed_note_does_not_erase_partial_answer_or_create_evidence(stage, monkeypatch):
    agent, project = stage
    authorize(agent, project)
    sid = source(project)
    project.background_agent['sources'][0]['shown_excerpts'] = ['Actually retrieved text about a limited study.']
    monkeypatch.setattr(agent, 'call', lambda *args, **kwargs: ReadingNotes(answer='资料不足，只能给出有限说明。',
        assessments=[action('assess_source', source_id=sid, decision='keep', quote='An invented quote is not evidence.')]))
    agent.consolidate_reading(project)
    assert project.background_agent['partial_answer']['markdown']
    assert project.background_agent['note_validation_errors']
    assert project.background_agent['assessments'] == {}
    assert not project.evidence


def test_pdf_reader_extracts_bounded_text(monkeypatch):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject, DictionaryObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=400)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(b'BT /F1 10 Tf 10 100 Td (This paper discusses ventilation and deposition with substantial limitations. The findings depend on local conditions.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    monkeypatch.setattr(research_web, 'fetch_public', lambda url: (url, 'application/pdf', output.getvalue()))
    result = research_web.read_source_snapshot('https://example.org/paper.pdf')
    assert 'ventilation and deposition' in result['text']
    assert '[Page 1]' in result['text']
    assert result['content_type'] == 'application/pdf'
    assert not result['cache_truncated']


def test_html_removes_navigation_not_article(monkeypatch):
    body = '<title>Actual paper</title><nav>irrelevant menu</nav><article>' + 'Scientific results and limitations. ' * 5 + '</article><footer>legal menu</footer>'
    monkeypatch.setattr(research_web, 'fetch_public', lambda url: (url, 'text/html', body.encode()))
    result = research_web.read_source_snapshot('https://example.org/paper')
    assert 'irrelevant menu' not in result['text'] and 'legal menu' not in result['text']
    assert 'Scientific results' in result['text']


def test_search_duplicate_annotation_enriches_missing_title(monkeypatch):
    monkeypatch.setattr('src.ai_scientist.tools.search_tools.extract_response_sources', lambda r:
        [{'url': 'https://example.org/paper', 'title': 'A real provider title'}])
    result = _search_candidates({'output': [{'type': 'web_search_call', 'action': {
        'sources': [{'url': 'https://example.org/paper'}]}}]}, 'query')
    assert len(result) == 1 and result[0].title == 'A real provider title'


def test_read_phrase_records_only_returned_range(stage, monkeypatch):
    agent, project = stage
    authorize(agent, project)
    sid = source(project)
    text = 'x' * 14000 + 'Ventilation mechanism' + 'y' * 14000
    monkeypatch.setattr('src.ai_scientist.background_agent.read_source', lambda url:
        {'url': url, 'text': text, 'sha256': 'fixture', 'truncated': False})
    result = agent.perform(project, action('read_source', source_id=sid, query='ventilation mechanism'))
    assert result['offset'] == 13000
    assert result['read_ranges'] == [{'start': 13000, 'end': 25000}]
    assert len(result['text']) == 12000
