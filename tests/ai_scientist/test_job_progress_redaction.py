import json

import pytest

from src.ai_scientist.job_store import ResearchJobStore


@pytest.mark.parametrize('message', [
    'api_key=top-secret',
    'Authorization: Bearer top-secret',
    'API key: top-secret\n请继续。',
    '引用：api_key="top-secret", 随后是中文和 emoji 😀。',
    '路径 \\files\\data；api_key=top-secret',
])
def test_progress_redaction_never_corrupts_serialized_job(tmp_path, monkeypatch, message):
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-provider-credential')
    store = ResearchJobStore(tmp_path)
    job = store.create('project_test', 'ANALYSIS')
    store.record_model_progress(job.project_id, job.job_id, {
        'call_id': 'call-test', 'status': 'streaming', 'public_response': message,
        'human_input_request': '请提供样本数。',
    })
    saved = store.load(job.project_id, job.job_id)
    assert 'top-secret' not in saved.progress['public_response']
    assert '[REDACTED]' in saved.progress['public_response']
    assert saved.progress['human_input_request'] == '请提供样本数。'
    assert saved.progress['public_response'] == saved.model_updates[0]['public_response']
    store.record_model_progress(job.project_id, job.job_id, {
        'call_id': 'call-test', 'status': 'completed', 'public_response': '继续调用成功。',
    })
    assert store.load(job.project_id, job.job_id).model_updates[0]['status'] == 'completed'
    assert json.loads(store.job_path(job.project_id, job.job_id).read_text())['job_id'] == job.job_id


def test_unknown_nested_job_data_is_sanitized_without_changing_structure(tmp_path, monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-provider-credential')
    store = ResearchJobStore(tmp_path)
    job = store.create('project_test', 'ANALYSIS')
    job.result = {'unknown_nested': [{'text': 'api_key=top-secret'},
                                   {'deeper': {'provider': 'test-provider-credential', 'number': 3, 'flag': True}}],
                  'safe': '原样保留 "引号" 和 \\backslash\n换行'}
    store.save(job)
    saved = store.load(job.project_id, job.job_id)
    assert saved.result['safe'] == job.result['safe']
    assert saved.result['unknown_nested'][0]['text'] == 'api_key=[REDACTED]'
    assert saved.result['unknown_nested'][1]['deeper'] == {
        'provider': '[REDACTED_API_KEY]', 'number': 3, 'flag': True}
    assert job.result['unknown_nested'][0]['text'] == 'api_key=top-secret'  # no caller mutation


def test_progress_still_drops_unknown_nested_receipt_and_private_fields(tmp_path):
    store = ResearchJobStore(tmp_path)
    job = store.create('project_test', 'ANALYSIS')
    store.record_model_progress(job.project_id, job.job_id, {
        'call_id': 'call-test', 'status': 'completed', 'public_response': '已返回公开答复。',
        'reasoning_content': 'PRIVATE_REASONING', 'unknown_nested': {'text': 'PRIVATE_PROMPT'},
        'receipt': {'response_id': 'response-test', 'unknown_nested': {'text': 'PRIVATE_TOKEN'}},
        'provider_responses': [{'response_model': 'test-qwen', 'prompt': {'text': 'PRIVATE_PROMPT'}}],
        'token_usage': {'total_tokens': 9, 'unknown_nested': {'text': 'PRIVATE_TOKEN'}},
    })
    saved = store.load(job.project_id, job.job_id)
    text = saved.model_dump_json()
    assert 'PRIVATE_' not in text
    assert 'reasoning_content' not in text
    assert 'unknown_nested' not in text
    assert saved.progress['receipt'] == {'response_id': 'response-test'}
    assert saved.progress['provider_responses'] == [{'response_model': 'test-qwen'}]
    assert saved.progress['token_usage'] == {'total_tokens': 9}
