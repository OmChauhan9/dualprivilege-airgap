"""Meta Model API client (OpenAI-compatible). Never called by tests or CI."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from airgap.models.base import Completion, Message, Role
from airgap.models.budget import MAX_OUTPUT_TOKENS

DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"
REASONING_EFFORTS = ("low", "medium", "high")


class ForbiddenModelError(ValueError):
    pass


class MetaModel:
    """Standard-mode Muse Spark behind the ChatModel interface.

    `-contributor` models let Meta train on prompts and outputs, so they are refused.
    """

    reasoning_effort: str | None = None  # class default, so test doubles need not set it

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> None:
        if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORTS:
            raise ValueError(
                f"unknown reasoning effort {reasoning_effort!r}; choose from {REASONING_EFFORTS}"
            )
        if "contributor" in model:
            raise ForbiddenModelError(
                "contributor models are not allowed (Meta may train on prompts)"
            )
        if not api_key:
            raise ValueError("MODEL_API_KEY is empty")
        from openai import OpenAI  # imported lazily so the fake path needs no network setup

        self.name = model
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        # No SDK retries: each retry is another billed request, but only one usage would be seen.
        self._client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)

    @classmethod
    def from_env(cls) -> MetaModel:
        return cls(
            api_key=os.environ.get("MODEL_API_KEY", ""),
            model=os.environ.get("MODEL_NAME", DEFAULT_MODEL),
            base_url=os.environ.get("MODEL_BASE_URL", DEFAULT_BASE_URL),
            reasoning_effort=os.environ.get("MODEL_REASONING_EFFORT") or None,
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
            "max_tokens": MAX_OUTPUT_TOKENS,  # bounds the worst-case cost of every call
        }
        if self.reasoning_effort is not None:
            # muse-spark-1.3 spends output tokens thinking; a long think can use the whole limit
            # and leave no answer, so the effort can be lowered.
            kwargs["reasoning_effort"] = self.reasoning_effort
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
        # Missing or partial usage is flagged, so the budget guard charges the worst case.
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        known = isinstance(prompt_tokens, int) and isinstance(completion_tokens, int)
        return Completion(
            text=text,
            parsed=parsed,
            prompt_tokens=prompt_tokens if isinstance(prompt_tokens, int) else 0,
            completion_tokens=completion_tokens if isinstance(completion_tokens, int) else 0,
            latency_s=latency,
            usage_known=known,
        )
