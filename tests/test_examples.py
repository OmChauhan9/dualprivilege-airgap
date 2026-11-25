"""The examples in examples/ run offline, as written, in CI."""

from __future__ import annotations

import runpy
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_byo_tools_runs_offline_and_shows_each_outcome(capsys):
    runpy.run_path(str(EXAMPLES / "byo_tools.py"), run_name="__main__")
    out = capsys.readouterr().out
    assert "confirm? pay 45.00 USD" in out
    assert "reply: Paid 1 invoice(s)" in out
    assert "unsettled: pay 45.00 USD to 0x1ED6E4000000000000000000000000000000AA01" in out
    assert "outcome unknown (TimeoutError)" in out
