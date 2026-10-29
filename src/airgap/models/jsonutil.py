"""Tolerant JSON extraction for real models that wrap their answer in prose or code fences.

This only finds the first JSON object in a reply. It grants nothing: every caller still validates
the object against a strict schema (reader), the plan validator (orchestrator) or the action
protocol (baseline), and fails closed when it does not fit.
"""

from __future__ import annotations

import json
from typing import Any

_DECODER = json.JSONDecoder()


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Return the first JSON object (a dict) found in `text`, or None."""
    start = text.find("{")
    while start != -1:
        try:
            value, _ = _DECODER.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(value, dict):
            return value
        start = text.find("{", start + 1)
    return None
