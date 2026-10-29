"""Meta Model API client (OpenAI-compatible). Never called by tests or CI."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from airgap.models.base import Completion, Message, Role

DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"


class ForbiddenModelError(ValueError):
    pass


class MetaModel:
    """Standard-mode Muse Spark behind the ChatModel interface.

    `-contributor` models let Meta train on prompts and outputs, so they are refused.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        temperature: float = 0.7,
    ) -> None:
        if "contributor" in model:
            raise ForbiddenModelError(
                "contributor models are not allowed (Meta may train on prompts)"
            )
        if not api_key:
            raise ValueError("MODEL_API_KEY is empty")
        from openai import OpenAI  # imported lazily so the fake path needs no network setup

        self.name = model
        self.temperature = temperature
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    @classmethod
    def from_env(cls) -> MetaModel:
        return cls(
            api_key=os.environ.get("MODEL_API_KEY", ""),
            model=os.environ.get("MODEL_NAME", DEFAULT_MODEL),
            base_url=os.environ.get("MODEL_BASE_URL", DEFAULT_BASE_URL),
        )

    def complete(
        self,
        messages: list[Message],
        *,
        role: Role,
        schema: dict[str, Any] | None = None,
    ) -> Completion:
        kwargs: dict[str, Any] = {
            "model": self.name,
            "messages": messages,
            "temperature": self.temperature,
        }
        if schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": f"{role}_output", "schema": schema},
            }
        start = time.perf_counter()
        resp = self._client.chat.completions.create(**kwargs)
        latency = time.perf_counter() - start
        text = resp.choices[0].message.content or ""
        parsed = None
        if schema is not None:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
        usage = resp.usage
        return Completion(
            text=text,
            parsed=parsed,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            latency_s=latency,
        )
