"""Recompute the headline numbers the README cites from the frozen files in results/published/.

    uv run python scripts/frozen_numbers.py

Per run, per case (any of 3 trials) and paired by case, so a reader can check every claim.
"""

from __future__ import annotations

import json
from collections import defaultdict
from math import comb
from pathlib import Path

from airgap.eval.harness import DATA_DRIVEN
from airgap.eval.metrics import wilson

PUB = Path(__file__).parents[1] / "results" / "published"
CONDS = ("A_no_defense", "B_prompt_defense", "C_airgap", "C_airgap_strict_confirm",
         "C_airgap_approve_all")  # fmt: skip


def rate(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} ({k / n:.0%}) [{lo:.0%}, {hi:.0%}]" if n else "0/0"


def sign_test(wins: int, losses: int) -> float:
    """Two-sided exact sign test on the cases where the two conditions disagree."""
    n = wins + losses
    tail = sum(comb(n, i) for i in range(0, min(wins, losses) + 1)) / 2**n
    return min(1.0, 2 * tail)


def corpus(name: str) -> None:
    trials = json.loads((PUB / f"{name}.json").read_text())["trials"]
    print(f"== {name}: {len(trials)} runs")
    by = defaultdict(list)
    for t in trials:
        by[(t["condition"], t["case_id"])].append(t)
    for cond in CONDS:
        att = [t for t in trials if t["condition"] == cond and t["is_attack"]]
        ben = [t for t in trials if t["condition"] == cond and not t["is_attack"]]
        dd = [t for t in att if t["category"] in DATA_DRIVEN]
        cases = {t["case_id"] for t in att}
        dd_cases = {t["case_id"] for t in dd}
        hit = {c for c in cases if any(x["hijacked"] for x in by[(cond, c)])}
        t1 = [t for t in att if t["trial"] == 0]
        print(f"  {cond}")
        print(f"    hijacked per run        {rate(sum(t['hijacked'] for t in att), len(att))}")
        print(f"    goal per run            {rate(sum(t['goal_reached'] for t in att), len(att))}")
        print(f"    data-driven per run     {rate(sum(t['hijacked'] for t in dd), len(dd))}")
        print(f"    cases hijacked (any)    {rate(len(hit), len(cases))}")
        print(f"    data-driven cases (any) {rate(len(hit & dd_cases), len(dd_cases))}")
        print(f"    trial 1 only            {rate(sum(t['hijacked'] for t in t1), len(t1))}")
        print(f"    attacker paid           {rate(sum(t['attacker_paid'] for t in att), len(att))}")
        print(
            f"    unlisted requests       {rate(sum(bool(t['unlisted_requests']) for t in att), len(att))}"
        )
        print(f"    benign completed        {rate(sum(t['benign_ok'] for t in ben), len(ben))}")
        print(
            f"    errored                 {rate(sum(bool(t['error']) and not t['denied'] for t in trials if t['condition'] == cond), len(ben) + len(att))}"
        )
        print(f"    hijacked cases: {sorted(hit)}")
    cases = sorted({t["case_id"] for t in trials if t["is_attack"]})
    for a, c in (("A_no_defense", "C_airgap"), ("B_prompt_defense", "C_airgap")):

        def hijacks(cond: str, case: str) -> int:
            return sum(x["hijacked"] for x in by[(cond, case)])

        wins = sum(hijacks(a, k) > hijacks(c, k) for k in cases)
        losses = sum(hijacks(a, k) < hijacks(c, k) for k in cases)
        print(
            f"  paired by case, {a} vs {c}: {wins} cases worse unguarded, {losses} worse "
            f"airgapped, sign test p={sign_test(wins, losses):.3f}"
        )


def adaptive(names: list[str]) -> None:
    print(f"== adaptive: {', '.join(names)}")
    tot = defaultdict(lambda: [0, 0, 0, 0])
    usable_total = attempts_total = 0
    for name in names:
        d = json.loads((PUB / f"{name}.json").read_text())
        usable = [a for a in d["attempts"] if not a["attacker_error"]]
        usable_total += len(usable)
        attempts_total += len(d["attempts"])
        for a in usable:
            for cond, o in a["outcomes"].items():
                s = tot[cond]
                s[0] += o["got_through"]
                s[1] += o["hijacked"]
                s[2] += o["attacker_paid"]
                s[3] += 1
        through = defaultdict(set)
        for a in usable:
            for cond, o in a["outcomes"].items():
                if o["got_through"]:
                    through[cond].add(a["scenario"])
        print(
            f"  {name}: usable {len(usable)}/{len(d['attempts'])}; scenarios breached "
            + ", ".join(f"{c} {sorted(s)}" for c, s in through.items())
        )
    print(f"  pooled usable {usable_total}/{attempts_total}")
    for cond, (g, h, p, n) in tot.items():
        print(
            f"  {cond}: got through {rate(g, n)}, hijacked {rate(h, n)}, attacker paid {rate(p, n)}"
        )


if __name__ == "__main__":
    corpus("final_corpus_v2")
    adaptive(["adaptive_v2_s1", "adaptive_v2_s2", "adaptive_v2_s3"])
