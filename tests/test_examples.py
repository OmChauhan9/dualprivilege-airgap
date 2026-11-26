"""The examples in examples/ and the README quick start run offline, as written, in CI."""

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


def test_readme_quick_start_runs_offline_as_written(capsys):
    readme = (EXAMPLES.parent / "README.md").read_text()
    section = readme.split("## Quick start", 1)[1].split("\n## ", 1)[0]
    code = section.split("```python\n", 1)[1].split("```", 1)[0]
    exec(compile(code, "README.md quick start", "exec"), {})
    out = capsys.readouterr().out
    assert "paying 45.00 USD to 0x1ED6E4000000000000000000000000000000AA01" in out
    assert "Paid 1 invoice(s)" in out
