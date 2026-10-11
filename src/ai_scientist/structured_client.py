"""Qwen's public narrative alongside validated, internal workflow controls."""

from __future__ import annotations

import json
import os
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Generic, TypeVar

import httpx
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ValidationError, field_validator

from src.ai_scientist.exceptions import StructuredOutputError
from src.ai_scientist.model_registry import ModelRegistry
from src.ai_scientist.model_progress import current_progress_callback, emit_model_progress
from src.ai_scientist.schemas import utc_now
from src.model_utils import model_max_retries


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

OutputT = TypeVar("OutputT", bound=BaseModel)
_call_context: ContextVar[dict[str, Any] | None] = ContextVar("structured_model_call", default=None)

PUBLIC_RESPONSE_INSTRUCTIONS = """
In addition to the internal role fields, write public_response FIRST in the JSON
object. This field must contain a nonempty public answer. It is your direct answer
to the researcher, not a JSON summary rendered
by the application. Use the user's language (natural Chinese when the task is
Chinese), and write fluid, useful prose or Markdown in your own words. Let this
stage's actual work determine its length and structure; do not force a fixed set
of headings or repeat boilerplate. Explain the current findings, their actual
evidence, important uncertainty and the useful next step when relevant.
Lead with what changed in this stage. Usually use one short paragraph or a few
bullets; do not repeat the question, approved methodology or earlier limitations.
Put detailed implementation and audit material in the internal fields, not a
second full-length public plan. A final scientific report may be longer.
Distinguish plans, attempted actions and observed results. Never claim a tool was run, a source
was read or a result verified unless the supplied records establish it. Do not
expose private chain-of-thought, hidden reasoning or credentials; provide useful
conclusions and concise explanations instead. The remaining schema fields are
internal controls and must still satisfy the assigned role's schema.
Also return human_input_request, normally an empty string. Set it to a concrete,
natural-language question ONLY if the current stage cannot responsibly proceed
without a missing user decision or input. Say exactly what is needed and why.
It is not a future reminder or permission to approve your own execution; existing
human authorization gates remain mandatory. Reflect a genuine request in your
public_response, without inventing one just to fill the field.
""".strip()


class _PublicFields(BaseModel):
    public_response: str = ""
    human_input_request: str = ""


class _RequiredPublicFields(BaseModel):
    public_response: str
    human_input_request: str

    @field_validator('public_response')
    @classmethod
    def nonempty_public_response(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('public_response must contain your nonempty direct answer to the researcher')
        return value  # Preserve the provider's formatting, not a rewritten template.


@dataclass
class StructuredCallMetadata:
    agent_name: str
    requested_model: str
    actual_model: str
    fallback_used: bool
    started_at: datetime
    finished_at: datetime
    token_usage: dict[str, int] = field(default_factory=dict)
    model_calls: int = 0
    attempted_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    fallback_calls: int = 0
    provider_responses: list[dict[str, Any]] = field(default_factory=list)
    public_response: str = ""
    human_input_request: str = ""
    call_id: str = ""


@dataclass
class StructuredCallResult(Generic[OutputT]):
    value: OutputT
    metadata: StructuredCallMetadata


class StructuredQwenClient:
    """Call Qwen, validate one schema, and allow one explicit repair attempt."""

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry()
        self.retry_count = min(1, max(0, int(os.getenv("AI_SCIENTIST_STRUCTURED_RETRY", "1"))))
        self.http_client: httpx.Client | None = None
        self.client: OpenAI | None = None
        self.provider_responses: list[dict[str, Any]] = []
        self.request_options: dict[str, Any] = {}
        self.shared_context: dict[str, Any] | None = None

    def call(
        self,
        agent_name: str,
        instructions: str,
        payload: dict[str, Any],
        output_model: type[OutputT],
    ) -> StructuredCallResult[OutputT]:
        if self.shared_context is not None:
            from src.ai_scientist.research_context import SHARED_CONTEXT_INSTRUCTION
            payload = {'shared_research_context': self.shared_context, **payload}
            instructions += '\n' + SHARED_CONTEXT_INSTRUCTION
        context = {
            "call_id": f"model_call_{uuid.uuid4().hex}",
            "agent_name": agent_name,
            "output_schema": output_model.__name__,
            "model": self.registry.resolve(agent_name).requested_model,
        }
        token = _call_context.set(context)
        _progress("started", public_response="")
        try:
            result = self._call(agent_name, instructions, payload, output_model)
            result.metadata.call_id = context["call_id"]
            _progress(
                "completed", model=result.metadata.actual_model,
                public_response=result.metadata.public_response,
                human_input_request=result.metadata.human_input_request,
                provider_responses=result.metadata.provider_responses,
                receipt=result.metadata.provider_responses[-1] if result.metadata.provider_responses else {},
                token_usage=result.metadata.token_usage,
                attempted_calls=result.metadata.attempted_calls,
                successful_calls=result.metadata.successful_calls,
                failed_calls=result.metadata.failed_calls,
            )
            return result
        except Exception as exc:
            _progress("failure", error_type=type(exc).__name__, provider_responses=list(self.provider_responses))
            raise
        finally:
            _call_context.reset(token)

    def _call(
        self,
        agent_name: str,
        instructions: str,
        payload: dict[str, Any],
        output_model: type[OutputT],
    ) -> StructuredCallResult[OutputT]:
        resolution = self.registry.resolve(agent_name)
        self.provider_responses = []
        started_at = utc_now()
        requested_model = resolution.requested_model
        actual_model = resolution.actual_model
        fallback_used = resolution.fallback_used
        token_usage: dict[str, int] = {}
        model_calls = 0
        schema = _public_output_schema(output_model)
        messages = [
            {"role": "system", "content": f"{instructions}\n\n{PUBLIC_RESPONSE_INSTRUCTIONS}"},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task_input": payload,
                        "output_json_schema": schema,
                        "response_rule": "Return one JSON object, public_response first, then human_input_request and the internal role fields. Do not include hidden reasoning.",
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            },
        ]

        try:
            content, usage, actual_model, used_api_fallback = self._request_with_fallback(
                requested_model,
                resolution.fallback_model,
                messages,
            )
            fallback_used = fallback_used or used_api_fallback
            model_calls += 1 + int(used_api_fallback)
            _merge_usage(token_usage, usage)
            value = self._validate(content, output_model, require_public=bool(self.provider_responses))
        except (json.JSONDecodeError, ValidationError) as first_error:
            if self.retry_count < 1:
                raise StructuredOutputError(
                    f"{agent_name} returned invalid structured output: {first_error}"
                ) from first_error
            _progress("repair", model=actual_model, public_response="", reason="schema_validation")
            # A format repair must retain the task, evidence and role constraints.
            # Dropping them leaves the model to guess a scientifically plausible answer.
            repair_messages = messages + [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "validation_error": str(first_error),
                            "output_json_schema": schema,
                            "previous_output": content,
                            "repair_rule": "Repair the JSON schema without inventing research or dropping task constraints. Return one corrected JSON object with public_response first.",
                        },
                        ensure_ascii=False,
                    ),
                }
            ]
            repaired, usage, repaired_model, used_api_fallback = self._request_with_fallback(
                actual_model,
                resolution.fallback_model,
                repair_messages,
            )
            actual_model = repaired_model
            fallback_used = fallback_used or used_api_fallback
            model_calls += 1 + int(used_api_fallback)
            _merge_usage(token_usage, usage)
            try:
                value = self._validate(repaired, output_model, require_public=bool(self.provider_responses))
            except (json.JSONDecodeError, ValidationError) as second_error:
                raise StructuredOutputError(
                    f"{agent_name} failed schema validation after one repair: {second_error}"
                ) from second_error
            content = repaired

        return StructuredCallResult(
            value=value,
            metadata=StructuredCallMetadata(
                agent_name=agent_name,
                requested_model=requested_model,
                actual_model=actual_model,
                fallback_used=fallback_used,
                started_at=started_at,
                finished_at=utc_now(),
                token_usage=token_usage,
                model_calls=model_calls,
                attempted_calls=model_calls,
                successful_calls=model_calls,
                failed_calls=0,
                fallback_calls=1 if fallback_used else 0,
                provider_responses=list(self.provider_responses),
                public_response=_public_field(content, "public_response"),
                human_input_request=_public_field(content, "human_input_request"),
            ),
        )

    def _request_with_fallback(
        self,
        model: str,
        fallback_model: str,
        messages: list[dict[str, str]],
    ) -> tuple[str, dict[str, int], str, bool]:
        try:
            content, usage = self._request(model, messages)
            return content, usage, model, False
        except Exception as exc:
            if isinstance(exc, (TimeoutError, StructuredOutputError)) or not fallback_model or fallback_model == model:
                raise
            _progress("repair", model=fallback_model, public_response="", reason="provider_fallback", error_type=type(exc).__name__)
            content, usage = self._request(fallback_model, messages)
            return content, usage, fallback_model, True

    def _request(self, model: str, messages: list[dict[str, str]]) -> tuple[str, dict[str, int]]:
        client = self._get_client()
        if current_progress_callback() is not None:
            return self._request_stream(client, model, messages)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            **self._bounded_options(model),
        )
        content = response.choices[0].message.content or ""
        self.provider_responses.append({
            "requested_model": model,
            "response_model": getattr(response, "model", None),
            "response_id": getattr(response, "id", None),
            "request_id": getattr(response, "_request_id", None),
        })
        usage_data = response.usage.model_dump() if response.usage else {}
        usage = {
            key: int(value)
            for key, value in usage_data.items()
            if key in {"prompt_tokens", "completion_tokens", "total_tokens"} and isinstance(value, int)
        }
        return content, usage

    def _request_stream(
        self, client: OpenAI, model: str, messages: list[dict[str, str]],
    ) -> tuple[str, dict[str, int]]:
        options = self._bounded_options(model)
        options.update(stream=True, stream_options={"include_usage": True})
        deadline = float(os.getenv('AI_SCIENTIST_MODEL_TOTAL_TIMEOUT', '300'))
        options['timeout'] = min(deadline, float(os.getenv('AI_SCIENTIST_MODEL_TIMEOUT', '90')))
        started = time.monotonic()
        response = client.chat.completions.create(
            model=model, messages=messages, response_format={"type": "json_object"}, **options,
        )
        receipt = {
            "requested_model": model, "response_model": None, "response_id": None,
            "request_id": getattr(response, "_request_id", None), "status": "streaming",
        }
        provider_http_response = getattr(response, "response", None)
        if provider_http_response is not None:
            receipt["request_id"] = provider_http_response.headers.get("x-request-id") or receipt["request_id"]
        self.provider_responses.append(receipt)
        content = ""
        usage: dict[str, int] = {}
        last_text = ""
        last_emitted = 0.0
        last_heartbeat = started
        chunks = 0
        finish_reason = None
        try:
            for chunk in response:
                now = time.monotonic()
                if now - started > deadline:
                    raise TimeoutError('Model call exceeded its total time budget; saved research is unchanged')
                chunks += 1
                receipt["response_model"] = getattr(chunk, "model", None) or receipt["response_model"]
                receipt["response_id"] = getattr(chunk, "id", None) or receipt["response_id"]
                receipt["request_id"] = getattr(chunk, "_request_id", None) or receipt["request_id"]
                if getattr(chunk, "usage", None) is not None:
                    usage = _usage_dict(chunk.usage)
                choices = getattr(chunk, "choices", [])
                if now - last_heartbeat >= 2:
                    _progress('streaming' if last_text else 'working', model=model,
                              public_response=last_text, receipt=dict(receipt),
                              elapsed_seconds=round(now-started, 1), received_chunks=chunks,
                              output_chars=len(content))
                    last_heartbeat = now
                if not choices:
                    continue
                finish_reason = getattr(choices[0], 'finish_reason', None) or finish_reason
                # Deliberately ignore reasoning_content and all other private fields.
                delta = getattr(choices[0].delta, "content", None)
                if not isinstance(delta, str):
                    continue
                content += delta
                text = extract_public_response(content)
                now = time.monotonic()
                if text != last_text and (not last_text or now - last_emitted >= 0.2):
                    _progress("streaming", model=model, public_response=text, receipt=dict(receipt))
                    last_text, last_emitted = text, now
            text = extract_public_response(content)
            if text != last_text:
                _progress("streaming", model=model, public_response=text, receipt=dict(receipt))
            receipt["status"] = "completed"
            if finish_reason == 'length':
                raise StructuredOutputError('Model output budget exhausted; incomplete output was not approved or executed')
        except Exception:
            receipt["status"] = "failed"
            raise
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        return content, usage

    def _bounded_options(self, model: str) -> dict[str, Any]:
        """Explicit budgets for hybrid Qwen; never rely on provider thinking defaults.

        Preserve project overrides. Do not send Qwen-specific parameters to other
        providers or thinking-only preview models. No private reasoning is stored.
        """
        options = dict(self.request_options)
        options.setdefault('max_tokens', int(os.getenv('AI_SCIENTIST_MAX_OUTPUT_TOKENS', '16000')))
        hybrid = model.startswith(('qwen3.8-max', 'qwen3.8-flash', 'qwen3.7-plus',
                                   'qwen3.7-max', 'qwen3.6-', 'qwen3.5-')) and 'preview' not in model
        if hybrid:
            role = (_call_context.get() or {}).get('agent_name', '')
            routine = role in {'domain_router', 'domain_analyst', 'method_selector', 'claim_mapper', 'evidence_researcher'}
            routine = routine or (_call_context.get() or {}).get('output_schema') in {'DomainSelectionOutput', 'ClaimEvidenceMappingResult'}
            extra = dict(options.get('extra_body') or {})
            extra.setdefault('enable_thinking', not routine)
            if extra['enable_thinking']:
                extra.setdefault('thinking_budget', int(os.getenv('AI_SCIENTIST_THINKING_BUDGET', '2048')))
            options['extra_body'] = extra
        return options

    def _get_client(self) -> OpenAI:
        """Create the provider client only when a real model call is attempted."""

        if self.client is not None:
            return self.client
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is missing. Please set it in .env.")
        self.http_client = httpx.Client(
            timeout=float(os.getenv("AI_SCIENTIST_MODEL_TIMEOUT", "90")),
            trust_env=False,
        )
        self.client = OpenAI(
            api_key=api_key,
            base_url=os.getenv(
                "AI_SCIENTIST_BASE_URL",
                os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            ),
            http_client=self.http_client,
            max_retries=model_max_retries(),
        )
        return self.client

    @staticmethod
    def _validate(content: str, output_model: type[OutputT], *, require_public: bool = False) -> OutputT:
        data = json.loads(content)
        if require_public:
            # Real provider calls must supply the public protocol themselves.
            # Receipt-free legacy test doubles remain compatible, but production
            # never invents a template when a model omits its direct answer.
            _RequiredPublicFields.model_validate(data)
        if isinstance(data, dict):
            _PublicFields.model_validate({
                key: data[key] for key in ("public_response", "human_input_request")
                if key in data and key not in output_model.model_fields
            })
            for key in ("public_response", "human_input_request"):
                if key not in output_model.model_fields:
                    data.pop(key, None)
        return output_model.model_validate(data)


def _public_output_schema(output_model: type[BaseModel]) -> dict[str, Any]:
    schema = output_model.model_json_schema()
    properties = schema.get("properties", {})
    schema["properties"] = {
        "public_response": {"type": "string", "minLength": 1, "description": "Your nonempty natural-language answer directly to the researcher; write this field first. Never private chain-of-thought."},
        "human_input_request": {"type": "string", "description": "Empty unless this stage requires a missing user decision or input; otherwise ask a specific question."},
        **properties,
    }
    schema["required"] = list(dict.fromkeys(["public_response", "human_input_request", *schema.get("required", [])]))
    return schema


def _public_field(content: str, key: str) -> str:
    value = json.loads(content)
    result = value.get(key, "") if isinstance(value, dict) else ""
    return result if isinstance(result, str) else ""


def _progress(status: str, **fields: Any) -> None:
    context = _call_context.get() or {}
    emit_model_progress({**context, "status": status, "timestamp": utc_now().isoformat(), **fields})


def _usage_dict(usage: Any) -> dict[str, int]:
    data = usage.model_dump() if hasattr(usage, "model_dump") else usage
    return {
        key: value for key, value in data.items()
        if key in {"prompt_tokens", "completion_tokens", "total_tokens"} and isinstance(value, int)
    } if isinstance(data, dict) else {}


def extract_public_response(content: str) -> str:
    """Decode only a top-level public_response string from an incomplete JSON object.

    No regex search: nested keys, quoted keys inside prose and arbitrary trailing
    JSON must never be mistaken for public output. Incomplete escapes are held
    back until valid, including UTF-16 surrogate pairs split across stream chunks.
    """
    decoder = json.JSONDecoder()
    cursor = len(content) - len(content.lstrip())
    if cursor >= len(content) or content[cursor] != "{":
        return ""
    cursor += 1
    try:
        while cursor < len(content):
            cursor = _skip_space(content, cursor)
            key, cursor = decoder.raw_decode(content, cursor)
            if not isinstance(key, str):
                return ""
            cursor = _skip_space(content, cursor)
            if cursor >= len(content) or content[cursor] != ":":
                return ""
            cursor = _skip_space(content, cursor + 1)
            if key == "public_response":
                return _partial_json_string(content, cursor)
            _, cursor = decoder.raw_decode(content, cursor)
            cursor = _skip_space(content, cursor)
            if cursor >= len(content) or content[cursor] != ",":
                return ""
            cursor += 1
    except (json.JSONDecodeError, ValueError):
        return ""
    return ""


def _skip_space(content: str, cursor: int) -> int:
    while cursor < len(content) and content[cursor].isspace():
        cursor += 1
    return cursor


def _partial_json_string(content: str, start: int) -> str:
    if start >= len(content) or content[start] != '"':
        return ""
    cursor = start + 1
    safe_end = cursor
    while cursor < len(content):
        char = content[cursor]
        if char == '"':
            return json.loads(content[start:cursor + 1])
        if char == "\\":
            if cursor + 1 >= len(content):
                break
            escape = content[cursor + 1]
            length = 6 if escape == "u" else 2
            if cursor + length > len(content):
                break
            if escape == "u":
                unit = int(content[cursor + 2:cursor + 6], 16)
                if 0xD800 <= unit <= 0xDBFF:
                    if cursor + 12 > len(content):
                        break
                    if content[cursor + 6:cursor + 8] != "\\u":
                        return ""
                    tail = int(content[cursor + 8:cursor + 12], 16)
                    if not 0xDC00 <= tail <= 0xDFFF:
                        return ""
                    length = 12
                elif 0xDC00 <= unit <= 0xDFFF:
                    return ""
            cursor += length
        else:
            if ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF:
                return ""
            cursor += 1
        safe_end = cursor
    return json.loads(content[start:safe_end] + '"')


def _merge_usage(total: dict[str, int], usage: dict[str, int]) -> None:
    for key, value in usage.items():
        total[key] = total.get(key, 0) + value
