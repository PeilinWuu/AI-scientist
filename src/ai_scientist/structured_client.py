"""Validated JSON-only Qwen calls for isolated AI Scientist agents."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Generic, TypeVar

import httpx
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from src.ai_scientist.exceptions import StructuredOutputError
from src.ai_scientist.model_registry import ModelRegistry
from src.ai_scientist.schemas import utc_now


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

OutputT = TypeVar("OutputT", bound=BaseModel)


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

    def call(
        self,
        agent_name: str,
        instructions: str,
        payload: dict[str, Any],
        output_model: type[OutputT],
    ) -> StructuredCallResult[OutputT]:
        resolution = self.registry.resolve(agent_name)
        started_at = utc_now()
        requested_model = resolution.requested_model
        actual_model = resolution.actual_model
        fallback_used = resolution.fallback_used
        token_usage: dict[str, int] = {}
        model_calls = 0
        schema = output_model.model_json_schema()
        messages = [
            {"role": "system", "content": instructions},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task_input": payload,
                        "output_json_schema": schema,
                        "response_rule": "Return one JSON object only. Do not include hidden reasoning.",
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
            value = self._validate(content, output_model)
        except (json.JSONDecodeError, ValidationError) as first_error:
            if self.retry_count < 1:
                raise StructuredOutputError(
                    f"{agent_name} returned invalid structured output: {first_error}"
                ) from first_error
            repair_messages = [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "validation_error": str(first_error),
                            "output_json_schema": schema,
                            "previous_output": content,
                            "repair_rule": "Return one corrected JSON object only.",
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
                value = self._validate(repaired, output_model)
            except (json.JSONDecodeError, ValidationError) as second_error:
                raise StructuredOutputError(
                    f"{agent_name} failed schema validation after one repair: {second_error}"
                ) from second_error

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
        except Exception:
            if not fallback_model or fallback_model == model:
                raise
            content, usage = self._request(fallback_model, messages)
            return content, usage, fallback_model, True

    def _request(self, model: str, messages: list[dict[str, str]]) -> tuple[str, dict[str, int]]:
        client = self._get_client()
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or ""
        usage_data = response.usage.model_dump() if response.usage else {}
        usage = {
            key: int(value)
            for key, value in usage_data.items()
            if key in {"prompt_tokens", "completion_tokens", "total_tokens"} and isinstance(value, int)
        }
        return content, usage

    def _get_client(self) -> OpenAI:
        """Create the provider client only when a real model call is attempted."""

        if self.client is not None:
            return self.client
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is missing. Please set it in .env.")
        self.http_client = httpx.Client(
            timeout=float(os.getenv("AI_SCIENTIST_MODEL_TIMEOUT", os.getenv("LLM_TIMEOUT", "300"))),
            trust_env=False,
        )
        self.client = OpenAI(
            api_key=api_key,
            base_url=os.getenv(
                "AI_SCIENTIST_BASE_URL",
                os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            ),
            http_client=self.http_client,
        )
        return self.client

    @staticmethod
    def _validate(content: str, output_model: type[OutputT]) -> OutputT:
        data = json.loads(content)
        return output_model.model_validate(data)


def _merge_usage(total: dict[str, int], usage: dict[str, int]) -> None:
    for key, value in usage.items():
        total[key] = total.get(key, 0) + value
