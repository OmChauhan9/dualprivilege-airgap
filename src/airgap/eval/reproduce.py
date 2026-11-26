"""Re-run every offline number, re-derive every recorded real-model number, check the docs.

    uv run airgap reproduce [--out PATH]

Exit 0 when everything matches, 1 on any mismatch, 2 on a setup error (a missing file or a
corrupt JSON). It never builds a real model and never reads the spend ledger.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import comb
from pathlib import Path
from typing import Any, cast

from airgap.eval import adaptive
from airgap.eval.corpus import DATA_DRIVEN
from airgap.eval.harness import run_suite
from airgap.eval.machine import REPO_ROOT, machine
from airgap.eval.metrics import Rate
from airgap.models.fake import FakeLLM
from airgap.server.replay import record_all
from airgap.world.scenarios import CATALOG, SCENARIOS
from airgap.world.tools import WorldTools

PUBLISHED = Path("results") / "published"
REPLAY = Path("web") / "public" / "replays" / "fake.json"
EXPECTED = PUBLISHED / "expected_numbers.json"
SEED = "none needed (deterministic)"
# The offline adaptive command the README quotes.
ADAPTIVE_ROUNDS = 5
ADAPTIVE_SCENARIOS = ("payment", "steering")
# Wall-clock time and the machine that ran it change on every run; nothing else may.
IGNORED = frozenset({"latency_s", "run_meta"})
MAX_LISTED = 20

# Each recorded trial set: (file, the one condition taken from it, or None for all of them).
TRIAL_SETS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "final_corpus_v2": (("final_corpus_v2", None),),
    "final_heldout": (("final_heldout", None),),
    # The README's tuning table takes each row from the run that measured it.
    "tuning": (
        ("real_A_tuning", None),
        ("real_BC_tuning", "B_prompt_defense"),
        ("real_C_tuning_v3", None),
    ),
    "real_BC_tuning": (("real_BC_tuning", None),),
}
ADAPTIVE_SETS: dict[str, tuple[str, ...]] = {
    "adaptive_v2": ("adaptive_v2_s1", "adaptive_v2_s2", "adaptive_v2_s3"),
    "adaptive_real": ("adaptive_real",),
}
OFFLINE_FILES = ("offline_fakellm", "offline_adaptive")
PUBLISHED_FILES = (
    *OFFLINE_FILES,
    *dict.fromkeys(f for files in TRIAL_SETS.values() for f, _ in files),
    *(f for files in ADAPTIVE_SETS.values() for f in files),
)
SPEND = re.compile(r"recorded real-model spend: \$([0-9.]+)")

Value = Rate | str | tuple[str, ...]
Numbers = dict[str, Value]
Trials = list[dict[str, Any]]


class SetupError(Exception):
    """Something to compare is missing or unreadable, so nothing was compared."""


# ---- how a number is written in the docs -------------------------------------------------


def _pct(x: float) -> str:
    return f"{x:.0%}"


def _with_pct(r: Rate) -> str:
    return f"{r.k}/{r.n}" + (f" ({_pct(r.point)})" if r.k else "")


def _bounds(r: Rate) -> str:
    lo, hi = r.interval()
    return f"[{_pct(lo)}, {_pct(hi)}]"


def _report(r: Rate) -> str:
    """The format scripts/frozen_numbers.py prints."""
    lo, hi = r.interval()
    return f"{r.k}/{r.n} ({r.k / r.n:.0%}) [{lo:.0%}, {hi:.0%}]" if r.n else "0/0"


FORMATS: dict[str, Callable[[Rate], str]] = {
    "k": lambda r: str(r.k),
    "n": lambda r: f"{r.n:,}",
    "frac": lambda r: f"{r.k}/{r.n}",
    "of": lambda r: f"{r.k} of {r.n}",
    "times": lambda r: f"{r.k} times in {r.n:,}",
    "pct": _with_pct,
    "ci": lambda r: f"{_with_pct(r)} {_bounds(r)}",
    "iv": lambda r: f"{r.k}/{r.n} {_bounds(r)}",
    "bounds": _bounds,
    "dec": lambda r: "{}/{} [{:.2f}, {:.2f}]".format(r.k, r.n, *r.interval()),
    "range": lambda r: "{:.0f}-{:.0f}%".format(*(100 * x for x in r.interval())),
    "hi": lambda r: _pct(r.interval()[1]),
    "cli": str,
    "cli_ci": lambda r: "95% CI {:.0%}-{:.0%}".format(*r.interval()),
    "json": lambda r: '{{"k": {}, "n": {}, "lo": {:.2f}, "hi": {:.2f}}}'.format(
        r.k, r.n, *r.interval()
    ),
    "report": _report,
}


# ---- deriving the numbers ----------------------------------------------------------------


def _rate(rows: Trials, key: str) -> Rate:
    return Rate(sum(bool(t[key]) for t in rows), len(rows))


def _errored(t: dict[str, Any]) -> bool:
    """An error that was not a refusal. Files saved before `denied` existed count every error."""
    return bool(t["error"]) and not t.get("denied", False)


def _error_kinds(error: str) -> tuple[str, ...]:
    """The exception name, and for a refused fetch whether the domain or the status refused it."""
    kind = error.split(":")[0]
    if kind != "FetchError":
        return (kind,)
    if "not named by the user" in error:
        return (kind, f"{kind}/unlisted_domain")
    return (kind, f"{kind}/http" if re.search(r"\bHTTP \d{3}\b", error) else f"{kind}/other")


def sign_test(wins: int, losses: int) -> float:
    """Two-sided exact sign test on the cases where the two conditions disagree."""
    n = wins + losses
    tail = sum(comb(n, i) for i in range(0, min(wins, losses) + 1)) / 2**n
    return min(1.0, 2 * tail)


def trial_numbers(prefix: str, trials: Trials) -> Numbers:
    """Every count the docs read from a file of trials, keyed `<prefix>/<condition>/<metric>`."""
    by_case: dict[tuple[str, str], Trials] = defaultdict(list)
    for t in trials:
        by_case[t["condition"], t["case_id"]].append(t)
    attacks = [t for t in trials if t["is_attack"]]
    cases = sorted({t["case_id"] for t in attacks})
    dd_cases = {t["case_id"] for t in attacks if t["category"] in DATA_DRIVEN}
    conds = list(dict.fromkeys(t["condition"] for t in trials))
    out: Numbers = {
        f"{prefix}/runs": f"{len(trials):,}",
        f"{prefix}/cases": str(len({t["case_id"] for t in trials})),
        f"{prefix}/attack_cases": str(len(cases)),
        f"{prefix}/benign_cases": str(len({t["case_id"] for t in trials if not t["is_attack"]})),
        f"{prefix}/dd_cases": str(len(dd_cases)),
        f"{prefix}/instruction_cases": str(len(cases) - len(dd_cases)),
        f"{prefix}/trials_per_case": str(max(t["trial"] for t in trials) + 1),
        f"{prefix}/heldout_attack_cases": str(len({t["case_id"] for t in attacks if t["heldout"]})),
    }
    paid = [_rate([t for t in attacks if t["condition"] == c], "attacker_paid") for c in conds]
    out[f"{prefix}/all_conditions/attacker_paid"] = Rate(
        sum(r.k for r in paid), sum(r.n for r in paid)
    )
    out[f"{prefix}/every_condition/attacker_paid"] = max(paid, key=lambda r: r.k)
    flagged = [
        _rate([t for t in trials if t["condition"] == c and not t["is_attack"]], "hijacked")
        for c in conds
    ]
    out[f"{prefix}/every_condition/benign_flagged"] = max(flagged, key=lambda r: r.k)
    errored = sum(_errored(t) for t in trials)
    out[f"{prefix}/errored_summary"] = f"{errored} runs errored." if errored else "No run errored."
    for cond in conds:
        rows = [t for t in trials if t["condition"] == cond]
        att = [t for t in rows if t["is_attack"]]
        ben = [t for t in rows if not t["is_attack"]]
        dd = [t for t in att if t["category"] in DATA_DRIVEN]
        instruction = [t for t in att if t["category"] not in DATA_DRIVEN]
        hit = {c for c in cases if any(x["hijacked"] for x in by_case[cond, c])}
        k = f"{prefix}/{cond}/"
        out |= {
            k + "hijacked": _rate(att, "hijacked"),
            k + "goal": _rate(att, "goal_reached"),
            k + "goal_only": Rate(
                sum(t["goal_reached"] and not t["hijacked"] for t in att), len(att)
            ),
            k + "attacker_paid": _rate(att, "attacker_paid"),
            k + "unlisted": _rate(att, "unlisted_requests"),
            k + "benign": _rate(ben, "benign_ok"),
            k + "benign_flagged": _rate(ben, "hijacked"),
            k + "errored": Rate(sum(_errored(t) for t in rows), len(rows)),
            k + "dd/hijacked": _rate(dd, "hijacked"),
            k + "instruction/hijacked": _rate(instruction, "hijacked"),
            k + "trial1/hijacked": _rate([t for t in att if t["trial"] == 0], "hijacked"),
            k + "heldout/hijacked": _rate([t for t in att if t["heldout"]], "hijacked"),
            k + "cases_hijacked": Rate(len(hit), len(cases)),
            k + "dd/cases_hijacked": Rate(len(hit & dd_cases), len(dd_cases)),
            k + "hijacked_cases": tuple(sorted(hit)),
            # How many trials of each hijacked case were hijacked, e.g. "3" when all of them.
            k + "hijacked_case_runs": ", ".join(
                sorted({str(sum(x["hijacked"] for x in by_case[cond, c])) for c in hit})
            ),
            k + "confirmations": f"{sum(t['confirmations'] for t in rows) / len(rows):.2f}",
        }
        for case in cases:
            for metric, key in (("hijacked", "hijacked"), ("goal", "goal_reached")):
                out[f"{k}case/{case}/{metric}"] = _rate(by_case[cond, case], key)
        for cat in sorted({t["category"] for t in att}):
            out[f"{k}category/{cat}/hijacked"] = _rate(
                [t for t in att if t["category"] == cat], "hijacked"
            )
        errors = Counter(k for t in rows if _errored(t) for k in _error_kinds(str(t["error"])))
        for kind, count in errors.items():
            out[f"{k}error/{kind}"] = Rate(count, len(rows))
    for a, c in (("A_no_defense", "C_airgap"), ("B_prompt_defense", "C_airgap")):
        if a not in conds or c not in conds:
            continue

        def hijacks(cond: str, case: str) -> int:
            return sum(x["hijacked"] for x in by_case[cond, case])

        wins = sum(hijacks(a, case) > hijacks(c, case) for case in cases)
        losses = sum(hijacks(a, case) < hijacks(c, case) for case in cases)
        out |= {
            f"{prefix}/paired/{a}/{c}/wins": str(wins),
            f"{prefix}/paired/{a}/{c}/losses": str(losses),
            f"{prefix}/paired/{a}/{c}/p": f"{sign_test(wins, losses):.3f}",
        }
    return out


def _count(rows: Trials, cond: str, pred: Callable[[dict[str, Any]], bool]) -> Rate:
    return Rate(sum(bool(pred(a["outcomes"][cond])) for a in rows), len(rows))


def adaptive_numbers(prefix: str, sessions: list[dict[str, Any]]) -> Numbers:
    """Adaptive attempts pooled over sessions, plus what each session breached."""
    usable = [[a for a in s["attempts"] if not a["attacker_error"]] for s in sessions]
    pooled = [a for u in usable for a in u]
    out: Numbers = {
        f"{prefix}/usable": Rate(len(pooled), sum(len(s["attempts"]) for s in sessions)),
        f"{prefix}/rounds": str(sessions[0]["rounds"]),
        f"{prefix}/scenarios": str(len(sessions[0]["scenarios"])),
    }
    for i, (s, u) in enumerate(zip(sessions, usable, strict=True), 1):
        out[f"{prefix}/s{i}/usable"] = Rate(len(u), len(s["attempts"]))
    scenarios = sessions[0]["scenarios"]
    paid: list[Rate] = []
    for cond in sessions[0]["conditions"]:
        k = f"{prefix}/{cond}/"
        invoices = [a for a in pooled if a["scenario"].startswith("invoice")]
        paid.append(_count(pooled, cond, lambda o: o["attacker_paid"]))
        out |= {
            k + "got_through": _count(pooled, cond, lambda o: o["got_through"]),
            k + "hijacked": _count(pooled, cond, lambda o: o["hijacked"]),
            k + "attacker_paid": paid[-1],
            k + "goal_only": _count(pooled, cond, lambda o: o["got_through"] and not o["hijacked"]),
            k + "invoice_family/hijacked": _count(invoices, cond, lambda o: o["hijacked"]),
        }
        for sid in scenarios:
            rows = [a for a in pooled if a["scenario"] == sid]
            out[f"{k}scenario/{sid}/hijacked"] = _count(rows, cond, lambda o: o["hijacked"])
            amounts = {a["amount"] for a in rows if a["outcomes"][cond]["got_through"]}
            out[f"{k}scenario/{sid}/through_amounts"] = ", ".join(
                f"{x:.2f}" for x in sorted(x for x in amounts if x is not None)
            )
        breached = []
        for i, u in enumerate(usable, 1):
            hit = tuple(sorted({a["scenario"] for a in u if a["outcomes"][cond]["got_through"]}))
            out[f"{prefix}/s{i}/{cond}/breached"] = hit
            breached.append(str(len(hit)))
            out[f"{prefix}/s{i}/{cond}/breached_count"] = Rate(len(hit), len(scenarios))
            # A figure lie changes an amount and injects no text.
            out[f"{prefix}/s{i}/{cond}/lie_only/got_through"] = _count(
                [a for a in u if not a["payload"]], cond, lambda o: o["got_through"]
            )
        out[k + "breached_per_session"] = f"{', '.join(breached)} of {len(scenarios)}"
    out[f"{prefix}/every_condition/attacker_paid"] = max(paid, key=lambda r: r.k)
    return out


def page_numbers() -> Numbers:
    """Every page the scenarios serve: benign pages, each channel and each named attack."""
    routes = [
        resp
        for sc in CATALOG.values()
        for variant in (None, *sc.channels, *sc.attacks)
        for _, _, resp in sc.make_web(variant).snapshot()
    ]
    return {
        "pages/routes": str(len(routes)),
        "pages/distinct": str(len({(r.body, r.content_type) for r in routes})),
    }


def replay_numbers(replay: dict[str, Any]) -> Numbers:
    """What the sanitizer removed on each recorded page, as the demo prints it."""
    return {
        f"replay/{run}/{m['pane']}/sanitize_chars": f"{m['data']['chars_in']} -> "
        f"{m['data']['chars_out']}"
        for run, msgs in replay["runs"].items()
        for m in msgs
        if m["kind"] == "sanitize"
    }


def spend_numbers(logs: dict[str, str]) -> Numbers:
    """The spend ledger is cumulative across runs, so each log's spend line is a running total
    and the largest is the project's total. The invalid first run is inside that total; without
    it, what is left is what all published runs cost together, superseded tuning runs included."""

    def spent(invalid: bool) -> list[float]:
        return [
            float(m)
            for name, text in logs.items()
            if name.startswith("invalid/") == invalid
            for m in SPEND.findall(text)
        ]

    total = max(spent(False), default=None)
    if total is None:
        raise SetupError(f"no 'recorded real-model spend' line in the logs in {PUBLISHED}")
    return {
        "spend/total": f"${total:.2f}",
        "spend/published_runs": f"${total - max(spent(True), default=0.0):.0f}",
    }


def _trials(published: dict[str, Any], files: tuple[tuple[str, str | None], ...]) -> Trials:
    return [
        t
        for name, only in files
        for t in published[name]["trials"]
        if only in (None, t["condition"])
    ]


def example_numbers() -> Numbers:
    """The code example in docs/architecture.md, run as written."""
    sc = SCENARIOS["identity"]
    model = FakeLLM()
    res = sc.guard(model, sc.make_web("visible_text"), WorldTools()).run(sc.user_text)
    payments = [(p.payee, p.amount_usd) for p in res.ledger.payments]
    return {"example/architecture": f"{payments} {res.confirmations}"}


def recorded_numbers(published: dict[str, Any], logs: dict[str, str]) -> Numbers:
    out: Numbers = spend_numbers(logs)
    for prefix, files in TRIAL_SETS.items():
        out |= trial_numbers(prefix, _trials(published, files))
    for prefix, names in ADAPTIVE_SETS.items():
        out |= adaptive_numbers(prefix, [published[n] for n in names])
    return out


# ---- inputs (the boundary) ---------------------------------------------------------------


@dataclass(frozen=True)
class Expected:
    id: str
    value: str
    doc: str
    quote: str

    @property
    def base(self) -> str:
        return self.id.split("#")[0].partition("@")[0]

    @property
    def fmt(self) -> str:
        return self.id.split("#")[0].partition("@")[2]


@dataclass(frozen=True)
class NotCovered:
    doc: str
    quote: str
    reason: str


@dataclass(frozen=True)
class Inputs:
    published: dict[str, Any]
    replay: Any
    logs: dict[str, str]
    expected: tuple[Expected, ...]
    not_covered: tuple[NotCovered, ...]
    docs: dict[str, str]  # whitespace-collapsed, so a quote may span a line break


def _squash(text: str) -> str:
    return " ".join(text.split())


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError as exc:
        raise SetupError(f"cannot read {path}: {exc.strerror or exc}") from exc


def _json(path: Path) -> Any:
    try:
        return json.loads(_read(path))
    except json.JSONDecodeError as exc:
        raise SetupError(f"{path} is not valid JSON: {exc}") from exc


def _fields(spec: Any, names: tuple[str, ...]) -> list[str] | None:
    fields = [spec.get(f) if isinstance(spec, dict) else None for f in names]
    return cast(list[str], fields) if all(isinstance(v, str) and v for v in fields) else None


def _expected(raw: Any, path: Path) -> tuple[tuple[Expected, ...], tuple[NotCovered, ...]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("numbers"), dict):
        raise SetupError(f'{path} must hold {{"numbers": {{id: {{value, doc, quote}}}}}}')
    entries = []
    for number_id, spec in raw["numbers"].items():
        fields = _fields(spec, ("value", "doc", "quote"))
        if fields is None:
            raise SetupError(f"{path}: {number_id} needs a value, a doc and a quote")
        e = Expected(number_id, *fields)
        if e.fmt and e.fmt not in FORMATS:
            raise SetupError(f"{path}: {number_id} names an unknown format {e.fmt!r}")
        if _squash(e.value) not in _squash(e.quote):
            raise SetupError(f"{path}: the value of {number_id} is not inside its quote")
        entries.append(e)
    skipped = []
    for spec in raw.get("not_covered", ()):
        fields = _fields(spec, ("doc", "quote", "reason"))
        if fields is None:
            raise SetupError(f"{path}: each not_covered entry needs a doc, a quote and a reason")
        skipped.append(NotCovered(*fields))
    return tuple(entries), tuple(skipped)


def load(root: Path) -> Inputs:
    pub = root / PUBLISHED
    published = {name: _json(pub / f"{name}.json") for name in PUBLISHED_FILES}
    expected, not_covered = _expected(_json(root / EXPECTED), root / EXPECTED)
    names = {e.doc for e in expected} | {n.doc for n in not_covered}
    docs = {doc: _squash(_read(root / doc)) for doc in sorted(names)}
    logs = {
        p.relative_to(pub).as_posix(): _read(p)
        for p in sorted([*pub.glob("*.log"), *pub.glob("invalid/*.log")])
    }
    return Inputs(published, _json(root / REPLAY), logs, expected, not_covered, docs)


# ---- comparing ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    step: str
    id: str
    detail: str
    mismatches: tuple[dict[str, Any], ...] = ()


def _plain(x: Any) -> Any:
    """What the value looks like after a save and a load: tuples become lists."""
    return json.loads(json.dumps(x))


def diff(expected: Any, derived: Any, path: str = "") -> list[tuple[str, Any, Any]]:
    if isinstance(expected, dict) and isinstance(derived, dict):
        keys = sorted((expected.keys() | derived.keys()) - IGNORED)
        return [
            d
            for key in keys
            for d in diff(
                expected.get(key, "(missing)"), derived.get(key, "(missing)"), f"{path}.{key}"
            )
        ]
    if isinstance(expected, list) and isinstance(derived, list):
        out = (
            []
            if len(expected) == len(derived)
            else [(f"{path}.length", len(expected), len(derived))]
        )
        for i, (e, d) in enumerate(zip(expected, derived, strict=False)):
            out += diff(e, d, f"{path}[{i}]")
        return out
    return [] if expected == derived else [(path, expected, derived)]


def _file_check(name: str, expected: Any, derived: Any, detail: str) -> Check:
    found = diff(expected, derived)
    listed = tuple(
        {
            "id": f"{name}{path}",
            "expected": e,
            "derived": d,
            "doc": name,
            "reason": "differs from a fresh offline run",
        }
        for path, e, d in found[:MAX_LISTED]
    )
    more = len(found) - len(listed)
    if more > 0:
        listed += ({"id": name, "reason": f"{more} more differences not listed"},)
    return Check("offline", name, detail, listed)


def offline_step(inputs: Inputs) -> tuple[list[Check], Numbers]:
    """Re-run the offline eval, the offline adaptive run and the console replay."""
    trials = _plain([asdict(t) for t in run_suite(FakeLLM(), 1)])
    attempts = adaptive.run_adaptive(
        adaptive.FakeAttacker(), FakeLLM(), ADAPTIVE_SCENARIOS, ADAPTIVE_ROUNDS
    )
    attack = _plain(
        adaptive.file_payload(
            attempts, adaptive.FakeAttacker.name, FakeLLM.name, ADAPTIVE_ROUNDS, ADAPTIVE_SCENARIOS
        )
    )
    replay = _plain(record_all())
    every_scenario = adaptive.run_adaptive(
        adaptive.FakeAttacker(), FakeLLM(), tuple(adaptive.ADAPTIVE_SCENARIOS), ADAPTIVE_ROUNDS
    )
    every_payload = _plain(
        adaptive.file_payload(
            every_scenario,
            adaptive.FakeAttacker.name,
            FakeLLM.name,
            ADAPTIVE_ROUNDS,
            tuple(adaptive.ADAPTIVE_SCENARIOS),
        )
    )
    pub = inputs.published
    checks = [
        _file_check(
            "offline_fakellm.json",
            pub["offline_fakellm"],
            {"model": FakeLLM.name, "trials": trials},
            f"{len(trials)} trials, every field but latency_s",
        ),
        _file_check(
            "offline_adaptive.json",
            pub["offline_adaptive"],
            attack,
            f"{len(attempts)} attempts, {', '.join(ADAPTIVE_SCENARIOS)}, {ADAPTIVE_ROUNDS} rounds",
        ),
        _file_check(
            "web/public/replays/fake.json",
            inputs.replay,
            replay,
            f"{len(replay['runs'])} recorded runs, every event",
        ),
    ]
    numbers = (
        trial_numbers("offline", trials)
        | adaptive_numbers("offline_adaptive", [attack])
        | adaptive_numbers("offline_adaptive_all", [every_payload])
        | replay_numbers(replay)
        | page_numbers()
        | example_numbers()
        | {
            "offline_adaptive/command": f"--rounds {ADAPTIVE_ROUNDS} "
            f"--scenarios {','.join(ADAPTIVE_SCENARIOS)}"
        }
    )
    return checks, numbers


def render(numbers: Numbers, e: Expected) -> str | None:
    value = numbers.get(e.base)
    if value is None:
        return None
    if isinstance(value, Rate) and e.fmt:
        return FORMATS[e.fmt](value)
    if isinstance(value, str) and not e.fmt:
        return value
    raise SetupError(f"{e.id}: {e.base} cannot be written as {e.fmt or 'a plain value'}")


def number_check(e: Expected, numbers: Numbers, docs: dict[str, str]) -> Check:
    derived = render(numbers, e)
    reasons = []
    if derived != e.value:
        reasons.append("derived value differs" if derived is not None else "no longer derived")
    if _squash(e.quote) not in docs[e.doc]:
        reasons.append("quote no longer in the doc")
    mismatch = {"id": e.id, "expected": e.value, "derived": derived, "doc": e.doc}
    listed = tuple(mismatch | {"reason": r} for r in reasons)
    return Check("numbers", e.id, f"{e.value}  ({e.doc})", listed)


def not_covered_check(n: NotCovered, docs: dict[str, str]) -> Check:
    """A number listed as not covered must still be in its doc, or the list has gone stale."""
    listed = ()
    if _squash(n.quote) not in docs[n.doc]:
        listed = (
            {
                "id": n.quote,
                "expected": n.quote,
                "derived": None,
                "doc": n.doc,
                "reason": "not_covered quote no longer in the doc",
            },
        )
    return Check("not_covered", n.quote, f"({n.doc})", listed)


# ---- the command -------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def default_out(root: Path, commit: str | None) -> Path:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return root / "results" / "repro" / f"{stamp}-{(commit or 'nogit')[:7]}.json"


def _show(c: Check) -> list[str]:
    lines = [f"{'FAIL' if c.mismatches else 'ok':<4} {c.step:<8} {c.id}  {c.detail}"]
    for m in c.mismatches:
        if "expected" in m:
            lines.append(
                f"       {m['reason']}: {m['id']}  expected {m['expected']!r}  "
                f"derived {m['derived']!r}  doc {m['doc']}"
            )
        else:
            lines.append(f"       {m['reason']}")
    return lines


def is_checkout(root: Path) -> bool:
    return (root / PUBLISHED).is_dir() and (root / "pyproject.toml").is_file()


def find_root(start: Path) -> Path | None:
    """The nearest folder at or above `start` that is a checkout, else the installed one."""
    for folder in (start, *start.parents):
        if is_checkout(folder):
            return folder
    return REPO_ROOT if is_checkout(REPO_ROOT) else None


def run(root: Path | None, out: Path | None = None) -> int:
    started, clock = _now(), time.perf_counter()
    if root is None or not is_checkout(root):
        where = "no folder at or above the current one" if root is None else str(root)
        print(
            f"setup error: {where} is not a checkout of this repo (it needs {PUBLISHED}/ and "
            "pyproject.toml). Run from a checkout or pass --root PATH."
        )
        return 2
    try:
        inputs = load(root)
        offline, numbers = offline_step(inputs)
        numbers |= recorded_numbers(inputs.published, inputs.logs)
        checks = (
            offline
            + [number_check(e, numbers, inputs.docs) for e in inputs.expected]
            + [not_covered_check(n, inputs.docs) for n in inputs.not_covered]
        )
    except SetupError as exc:
        print(f"setup error: {exc}")
        return 2
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        print(f"setup error: a published file does not have the expected shape ({exc!r})")
        return 2
    for c in checks:
        print("\n".join(_show(c)))
    seconds = time.perf_counter() - clock
    failed = [c for c in checks if c.mismatches]
    info = machine(root)
    record = {
        "command": "airgap reproduce",
        "started_utc": started,
        "finished_utc": _now(),
        "seconds": round(seconds, 2),
        "seed": SEED,
        "machine": info,
        "checks": len(checks),
        "mismatches": len(failed),
        "steps": [
            {
                "name": step,
                "checks": sum(c.step == step for c in checks),
                "mismatches": [m for c in failed if c.step == step for m in c.mismatches],
            }
            for step in ("offline", "numbers", "not_covered")
        ],
        "not_covered": [asdict(n) for n in inputs.not_covered],
    }
    path = out or default_out(root, (info["git"] or {}).get("commit"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1) + "\n")
    print(
        f"\nreproduce: {len(checks)} checks, {len(failed)} mismatched, {seconds:.1f} s. "
        f"{len(inputs.not_covered)} of them check that a number listed as not covered in "
        f"{EXPECTED} is still in its doc. "
        f"Record: {path}"
    )
    return 1 if failed else 0


# ---- scripts/frozen_numbers.py -----------------------------------------------------------

_REPORT_CONDITIONS = (
    "A_no_defense",
    "B_prompt_defense",
    "C_airgap",
    "C_airgap_strict_confirm",
    "C_airgap_approve_all",
)
_REPORT_ROWS = (
    ("hijacked per run", "hijacked"),
    ("goal per run", "goal"),
    ("data-driven per run", "dd/hijacked"),
    ("cases hijacked (any)", "cases_hijacked"),
    ("data-driven cases (any)", "dd/cases_hijacked"),
    ("trial 1 only", "trial1/hijacked"),
    ("attacker paid", "attacker_paid"),
    ("unlisted requests", "unlisted"),
    ("benign completed", "benign"),
    ("errored", "errored"),
)


def _get[T](numbers: Numbers, key: str, kind: type[T]) -> T:
    value = numbers[key]
    assert isinstance(value, kind), key
    return value


def frozen_report(root: Path = REPO_ROOT) -> str:
    """The final-code corpus table and the Oct 31 adaptive sessions, per run and per case."""
    pub = root / PUBLISHED
    name = "final_corpus_v2"
    trials = _json(pub / f"{name}.json")["trials"]
    n = trial_numbers(name, trials)
    lines = [f"== {name}: {len(trials)} runs"]
    for cond in _REPORT_CONDITIONS:
        lines.append(f"  {cond}")
        for label, key in _REPORT_ROWS:
            lines.append(f"    {label:<23} {_report(_get(n, f'{name}/{cond}/{key}', Rate))}")
        hit = _get(n, f"{name}/{cond}/hijacked_cases", tuple)
        lines.append(f"    hijacked cases: {list(hit)}")
    for a in ("A_no_defense", "B_prompt_defense"):
        p = f"{name}/paired/{a}/C_airgap"
        lines.append(
            f"  paired by case, {a} vs C_airgap: {n[p + '/wins']} cases worse unguarded, "
            f"{n[p + '/losses']} worse airgapped, sign test p={n[p + '/p']}"
        )
    names = ADAPTIVE_SETS["adaptive_v2"]
    sessions = [_json(pub / f"{s}.json") for s in names]
    ad = adaptive_numbers("adaptive_v2", sessions)
    conds = sessions[0]["conditions"]
    lines.append(f"== adaptive: {', '.join(names)}")
    for i, s in enumerate(names, 1):
        breached = [(c, _get(ad, f"adaptive_v2/s{i}/{c}/breached", tuple)) for c in conds]
        usable = _get(ad, f"adaptive_v2/s{i}/usable", Rate)
        lines.append(
            f"  {s}: usable {usable.k}/{usable.n}; scenarios breached "
            + ", ".join(f"{c} {list(b)}" for c, b in breached if b)
        )
    pooled = _get(ad, "adaptive_v2/usable", Rate)
    lines.append(f"  pooled usable {pooled.k}/{pooled.n}")
    for c in conds:
        got, hij, paid = (
            _report(_get(ad, f"adaptive_v2/{c}/{m}", Rate))
            for m in ("got_through", "hijacked", "attacker_paid")
        )
        lines.append(f"  {c}: got through {got}, hijacked {hij}, attacker paid {paid}")
    return "\n".join(lines)
