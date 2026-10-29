"""Record/replay wrapper so demos and evals are reproducible and cheap."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from airgap.models.base import ChatModel, Completion, Message, Role

Mode = Literal["record", "replay", "passthrough"]


class CacheMiss(RuntimeError):
    pass


class CachedModel:
    def __init__(self, inner: ChatModel, directory: Path, mode: Mode = "record") -> None:
        self.inner = inner
        self.name = inner.name
        self.directory = directory
        self.mode = mode
        directory.mkdir(parents=True, exist_ok=True)

    def _key(self, messages: list[Message], role: Role, schema: dict[str, Any] | None) -> str:
        blob = json.dumps(
            {"m": self.inner.name, "msgs": messages, "role": role, "schema": schema},
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def complete(
        self,
        messages: list[Message],
        *,
        role: Role,
        schema: dict[str, Any] | None = None,
    ) -> Completion:
        if self.mode == "passthrough":
            return self.inner.complete(messages, role=role, schema=schema)
        path = self.directory / f"{self._key(messages, role, schema)}.json"
        if path.exists():
            return Completion(**json.loads(path.read_text()))
        if self.mode == "replay":
            raise CacheMiss(f"no recorded response for {path.name}")
        comp = self.inner.complete(messages, role=role, schema=schema)
        path.write_text(json.dumps(comp.__dict__))
        return comp
