"""Every figure on the front-door page matches the frozen results file it names."""

import json
import re
from pathlib import Path

from airgap.eval.metrics import wilson

ROOT = Path(__file__).parents[1]
PAGE = (ROOT / "web" / "landing.html").read_text()
TRIALS = json.loads((ROOT / "results" / "published" / "final_corpus_v2.json").read_text())["trials"]


def _rate(field: str, condition: str | None, attack: bool = True) -> tuple[int, int, float, float]:
    rows = [t for t in TRIALS if t["is_attack"] is attack and condition in (None, t["condition"])]
    k = sum(bool(t[field]) for t in rows)
    lo, hi = wilson(k, len(rows))
    return k, len(rows), lo * 100, hi * 100


def _figures() -> list[dict[str, str]]:
    return [
        dict(re.findall(r'data-([a-z]+)="([^"]*)"', m.group(0)))
        for m in re.finditer(r"<figure class=\"figure\"[^>]*>", PAGE)
    ]


def test_every_figure_names_the_frozen_file_and_matches_it():
    figures = _figures()
    assert len(figures) == 3
    for f in figures:
        assert f["source"] == "final_corpus_v2.json"
        condition = None if f["condition"] == "all" else f["condition"]
        k, n, lo, hi = _rate(f["field"], condition)
        assert (int(f["k"]), int(f["n"])) == (k, n)
        assert abs(float(f["lo"]) - lo) < 0.05 and abs(float(f["hi"]) - hi) < 0.05


def test_figures_in_the_text_match_the_frozen_file():
    k, n, lo, hi = _rate("hijacked", "A_no_defense")
    assert f"{k}/{n} with no defense" in PAGE and f"no defense {lo:.1f} to {hi:.1f}%" in PAGE
    k, n, lo, hi = _rate("hijacked", "C_airgap")
    assert f"{k}/{n}" in PAGE and f"airgap {lo:.1f} to {hi:.1f}%" in PAGE
    k, n, *_ = _rate("benign_ok", "C_airgap_strict_confirm", attack=False)
    assert f"{k}/{n} normal tasks done" in PAGE and f"finishes {k} of {n} normal tasks" in PAGE
    k, n, *_ = _rate("hijacked", "C_airgap")
    assert f"All {k} airgap hijacks" in PAGE
    k, n, *_ = _rate("attacker_paid", None)
    assert f"{k}/{n:,}" in PAGE
