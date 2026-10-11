from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, ValidationError

from src.ai_scientist.exceptions import StructuredOutputError
from src.ai_scientist.model_progress import (
    current_progress_callback,
    emit_model_progress,
    model_progress,
)
from src.ai_scientist.model_registry import ModelRegistry
from src.ai_scientist.structured_client import StructuredQwenClient, extract_public_response


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


def client() -> StructuredQwenClient:
    return StructuredQwenClient(ModelRegistry({"research_director": "test-qwen", "fallback": "test-qwen"}))


def test_public_output_preserves_free_text_and_internal_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    subject = client()
    calls = []
    narrative = "目前只能提出方案，尚未执行。\n\n**需要补充**采样时间，才能解释变化。"
    response = json.dumps({"public_response": narrative, "human_input_request": "请提供采样时间。", "value": "control"})

    def request(model, fallback, messages):
        calls.append(messages)
        return response, {"total_tokens": 9}, model, False

    monkeypatch.setattr(subject, "_request_with_fallback", request)
    events = []
    with model_progress(events.append):
        result = subject.call("research_director", "role instructions", {"objective": "解释实验"}, StrictOutput)

    assert result.value.model_dump() == {"value": "control"}
    assert result.metadata.public_response == narrative
    assert result.metadata.human_input_request == "请提供采样时间。"
    assert result.metadata.call_id.startswith("model_call_")
    assert [event["status"] for event in events] == ["started", "completed"]
    assert events[-1]["call_id"] == result.metadata.call_id
    assert events[-1]["public_response"] == narrative
    assert events[-1]["human_input_request"] == "请提供采样时间。"
    schema = json.loads(calls[0][1]["content"])["output_json_schema"]
    assert list(schema["properties"])[0] == "public_response"
    assert schema["additionalProperties"] is False
    assert "role instructions" in calls[0][0]["content"]
    assert "private chain-of-thought" in calls[0][0]["content"]


def test_only_reserved_extra_fields_are_removed() -> None:
    assert StructuredQwenClient._validate('{"value":"x","public_response":"hi","human_input_request":""}', StrictOutput).value == "x"
    with pytest.raises(ValidationError):
        StructuredQwenClient._validate('{"value":"x","invented_control":true}', StrictOutput)
    with pytest.raises(ValidationError):
        StructuredQwenClient._validate('{"value":"x","human_input_request":{"invented":"object"}}', StrictOutput)

    class ExistingPublicField(StrictOutput):
        public_response: str

    value = StructuredQwenClient._validate('{"value":"x","public_response":"keep"}', ExistingPublicField)
    assert value.public_response == "keep"


def test_legacy_mock_output_remains_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    subject = client()
    monkeypatch.setattr(subject, "_request_with_fallback", lambda *args: ('{"value":"old"}', {}, "test-qwen", False))
    result = subject.call("research_director", "role", {}, StrictOutput)
    assert result.value.value == "old"
    assert result.metadata.public_response == ""
    assert result.metadata.human_input_request == ""


def test_schema_repair_keeps_original_task_role_and_uses_repaired_public_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    subject = client()
    subject.retry_count = 1
    calls = []
    responses = iter([
        '{"public_response":"unvalidated","wrong":"x"}',
        '{"public_response":"修正后的公开答复","human_input_request":"","value":"fixed"}',
    ])

    def request(model, fallback, messages):
        calls.append(messages)
        return next(responses), {"total_tokens": 2}, model, False

    monkeypatch.setattr(subject, "_request_with_fallback", request)
    events = []
    with model_progress(events.append):
        result = subject.call("research_director", "Do not invent evidence.", {"feedback": "keep my constraints"}, StrictOutput)
    assert calls[1][:2] == calls[0]
    assert "keep my constraints" in calls[1][1]["content"]
    assert result.metadata.public_response == "修正后的公开答复"
    assert result.metadata.model_calls == 2
    assert result.metadata.token_usage == {"total_tokens": 4}
    assert [event["status"] for event in events] == ["started", "repair", "completed"]


def test_failure_progress_does_not_leak_provider_exception_body(monkeypatch: pytest.MonkeyPatch) -> None:
    subject = client()
    def fail(*args):
        raise RuntimeError("Authorization: Bearer secret-provider-key")
    monkeypatch.setattr(subject, "_request_with_fallback", fail)
    events = []
    with model_progress(events.append), pytest.raises(RuntimeError):
        subject.call("research_director", "role", {}, StrictOutput)
    assert events[-1]["status"] == "failure"
    assert events[-1]["error_type"] == "RuntimeError"
    assert "secret-provider-key" not in json.dumps(events)


def test_failed_repair_never_publishes_completed_output(monkeypatch: pytest.MonkeyPatch) -> None:
    subject = client()
    subject.retry_count = 1
    monkeypatch.setattr(subject, "_request_with_fallback", lambda *args: ('{"public_response":"not valid","wrong":1}', {}, "test-qwen", False))
    events = []
    with model_progress(events.append), pytest.raises(StructuredOutputError):
        subject.call("research_director", "role", {}, StrictOutput)
    assert [event["status"] for event in events] == ["started", "repair", "failure"]


@pytest.mark.parametrize("ensure_ascii", [True, False])
def test_partial_parser_is_safe_at_every_chunk_boundary(ensure_ascii: bool) -> None:
    public = '中文 😀\n引文说："检验"，路径 \\file，文本 public_response。'
    content = json.dumps({"public_response": public, "value": "PRIVATE_INTERNAL_CONTROL"}, ensure_ascii=ensure_ascii)
    for end in range(len(content) + 1):
        decoded = extract_public_response(content[:end])
        assert public.startswith(decoded)
        assert "PRIVATE_INTERNAL_CONTROL" not in decoded
        assert not any(0xD800 <= ord(char) <= 0xDFFF for char in decoded)
    assert extract_public_response(content) == public


@pytest.mark.parametrize("content", [
    '{"nested":{"public_response":"PRIVATE"},"value":1}',
    '{"value":"public_response: PRIVATE"}',
    '{"public_response":{"not":"a string"},"value":"PRIVATE"}',
    '{"public_response":null}',
    '[{"public_response":"PRIVATE"}]',
    'prefix {"public_response":"PRIVATE"}',
    '{"public_response":"\\uZZZZ"}',
    '{"public_response":"\\ud800x"}',
    '{"public_response":"\\udc00"}',
    '{"public_response":"\\x"}',
])
def test_partial_parser_never_treats_other_data_as_public_text(content: str) -> None:
    assert extract_public_response(content) == ""


def test_partial_parser_accepts_top_level_field_after_complete_internal_values() -> None:
    content = '{"nested":{"public_response":"PRIVATE"},"array":[1,2],"public_response":"公开答复","secret":"PRIVATE"}'
    assert extract_public_response(content) == "公开答复"


def test_stream_uses_actual_sdk_sse_without_reading_private_reasoning() -> None:
    requests = []
    body = json.dumps({"public_response": "公开输出\n只有实际依据。", "human_input_request": "", "value": "INTERNAL_ONLY"}, ensure_ascii=True)
    chunks = [
        {"id": "response-test", "object": "chat.completion.chunk", "model": "test-qwen", "created": 1,
         "choices": [{"index": 0, "delta": {"reasoning_content": "PRIVATE_REASONING_SECRET"}, "finish_reason": None}]},
        *[
            {"id": "response-test", "object": "chat.completion.chunk", "model": "test-qwen", "created": 1,
             "choices": [{"index": 0, "delta": {"content": body[index:index + 7]}, "finish_reason": None}]}
            for index in range(0, len(body), 7)
        ],
        {"id": "response-test", "object": "chat.completion.chunk", "model": "test-qwen", "created": 1,
         "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        sse = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream", "x-request-id": "request-test"}, content=sse)

    subject = client()
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        subject.client = OpenAI(api_key="not-real", base_url="https://provider.test/v1", http_client=transport, max_retries=0)
        events = []
        with model_progress(events.append):
            result = subject.call("research_director", "role", {}, StrictOutput)
    assert requests[0]["stream"] is True
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert result.metadata.public_response == "公开输出\n只有实际依据。"
    assert result.metadata.token_usage == {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
    assert result.metadata.provider_responses[0]["response_id"] == "response-test"
    assert result.metadata.provider_responses[0]["request_id"] == "request-test"
    assert result.metadata.provider_responses[0]["status"] == "completed"
    assert any(event["status"] == "streaming" for event in events)
    assert "PRIVATE_REASONING_SECRET" not in json.dumps(events)
    assert "INTERNAL_ONLY" not in json.dumps(events)


def test_no_callback_keeps_nonstream_provider_contract() -> None:
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"public_response":"直接答复","human_input_request":"","value":"x"}'))],
            usage=None, model="test-qwen", id="nonstream-test",
        )
    subject = client()
    subject.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = subject.call("research_director", "role", {}, StrictOutput)
    assert "stream" not in calls[0]
    assert result.metadata.provider_responses[0]["response_id"] == "nonstream-test"


@pytest.mark.parametrize('broken_fields', [
    {},
    {'public_response': '已有公开答复'},  # Missing the explicit input-request field.
    {'public_response': '', 'human_input_request': ''},
    {'public_response': ' \n\t ', 'human_input_request': ''},
    {'public_response': None, 'human_input_request': ''},
    {'public_response': '已有公开答复', 'human_input_request': None},
    {'public_response': '已有公开答复', 'human_input_request': {'question': 'not a string'}},
])
def test_real_provider_missing_or_invalid_public_fields_get_one_contextual_repair(broken_fields):
    subject = client()
    subject.retry_count = 1
    requests = []
    outputs = iter([
        json.dumps({'value': 'draft', **broken_fields}),
        json.dumps({'public_response': '保持原始格式。\n\n现在回答研究者。', 'human_input_request': '', 'value': 'fixed'}),
    ])
    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=next(outputs)))],
            usage=None, model='test-qwen', id=f'provider-{len(requests)}',
        )
    subject.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = subject.call('research_director', 'Keep this role and evidence.', {'feedback': 'Do not invent results.'}, StrictOutput)
    assert len(requests) == 2
    assert requests[1]['messages'][:2] == requests[0]['messages']
    assert result.value.value == 'fixed'
    assert result.metadata.public_response == '保持原始格式。\n\n现在回答研究者。'
    assert result.metadata.model_calls == 2
    assert len(result.metadata.provider_responses) == 2


def test_real_provider_never_substitutes_template_for_absent_public_answer():
    subject = client()
    subject.retry_count = 1
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"value":"valid internal control"}'))],
            usage=None, model='test-qwen', id=f'provider-{len(requests)}',
        )
    subject.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(StructuredOutputError, match='public_response'):
        subject.call('research_director', 'Role', {}, StrictOutput)
    assert len(requests) == 2


def test_real_provider_public_answer_is_not_forced_to_minimum_length_or_structure():
    subject = client()
    def create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"public_response":"可。","human_input_request":"","value":"x"}'))],
            usage=None, model='test-qwen', id='provider-one',
        )
    subject.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = subject.call('research_director', 'Role', {}, StrictOutput)
    assert result.metadata.public_response == '可。'
    assert result.metadata.model_calls == 1


def test_observer_failure_and_nested_context_do_not_affect_call() -> None:
    received = []
    def broken(event):
        raise RuntimeError("observer disconnected")
    assert current_progress_callback() is None
    with model_progress(received.append):
        with model_progress(broken):
            emit_model_progress({"status": "started"})
        emit_model_progress({"status": "completed"})
    assert received == [{"status": "completed"}]
    assert current_progress_callback() is None


def test_progress_callbacks_do_not_cross_concurrent_jobs() -> None:
    barrier = Barrier(2)
    def run(name):
        received = []
        with model_progress(received.append):
            barrier.wait(timeout=3)
            emit_model_progress({"job": name})
        return received
    with ThreadPoolExecutor(max_workers=2) as executor:
        left = executor.submit(run, "left")
        right = executor.submit(run, "right")
        assert left.result() == [{"job": "left"}]
        assert right.result() == [{"job": "right"}]
