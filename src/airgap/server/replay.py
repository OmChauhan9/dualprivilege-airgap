"""Record offline FakeLLM runs for the UI's deterministic replay mode.

    uv run python -m airgap.server.replay            # writes web/public/replays/fake.json

Replays are recorded with the offline FakeLLM only, so they are a plumbing demo, not evidence
about real models. tests/test_server.py fails if the committed file drifts from the pipeline.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from airgap.models.fake import FakeLLM
from airgap.server.runner import Message, run_both
from airgap.world.scenarios import SCENARIOS

DEFAULT_OUT = Path(__file__).parents[3] / "web" / "public" / "replays" / "fake.json"


def record_run(scenario: str, channel: str) -> list[Message]:
    msgs: list[Message] = []
    run_both(
        SCENARIOS[scenario], None if channel == "none" else channel, FakeLLM(), "fake", msgs.append
    )
    return msgs


def record_all() -> dict[str, object]:
    runs: dict[str, list[Message]] = {}
    for sc in SCENARIOS.values():
        for channel in ("none", *sc.channels):
            runs[f"{sc.id}:{channel}"] = record_run(sc.id, channel)
    return {
        "simulated": True,
        "model": FakeLLM.name,
        "note": "Offline FakeLLM simulation: plumbing check, not evidence about real models.",
        "runs": runs,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m airgap.server.replay")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record_all(), separators=(",", ":")) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
