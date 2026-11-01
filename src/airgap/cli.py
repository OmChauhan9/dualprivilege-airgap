"""Command line: `airgap demo | eval | adaptive | serve | spend`."""

from __future__ import annotations

import argparse
import os
import sys
from typing import cast

from airgap.baseline.vulnerable_agent import VulnerableAgent
from airgap.core.airgap import AirgapAgent
from airgap.core.audit import Event
from airgap.core.result import RunResult
from airgap.eval.corpus import Split, is_heldout
from airgap.models.base import ChatModel, RealCallsDisabled
from airgap.models.budget import BudgetExceeded
from airgap.models.fake import FakeLLM
from airgap.world.scenarios import SCENARIOS, Scenario

# Color only on a terminal, and never when NO_COLOR is set (https://no-color.org).
_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
RED, GREEN, DIM, BOLD, RESET = (
    ("\033[31m", "\033[32m", "\033[2m", "\033[1m", "\033[0m") if _COLOR else ("",) * 5
)


def make_model(provider: str) -> ChatModel:
    if provider == "fake":
        return FakeLLM()
    if provider == "meta":
        if os.environ.get("AIRGAP_ALLOW_REAL_CALLS") != "1":
            raise RealCallsDisabled(
                "Refusing real model calls: set AIRGAP_ALLOW_REAL_CALLS=1 only after confirming "
                "your Meta credit is visible, auto top-off is off and a spend limit is set."
            )
        from airgap.models.budget import BudgetedModel, cap_from_env
        from airgap.models.meta import MetaModel

        # Every real model is wrapped: a hard cap on total spend, remembered across runs.
        return BudgetedModel(MetaModel.from_env(), cap_usd=cap_from_env())
    sys.exit(f"unknown provider {provider}")


def _fmt(ev: Event) -> str:
    d = ev.data
    if ev.kind == "fetch":
        return f"fetch  origin={BOLD}{d['domain']}{RESET}  redirects={len(d['redirects']) - 1}"
    if ev.kind == "sanitize":
        removed = ", ".join(sorted({r["channel"] for r in d["removed"]})) or "nothing"
        return f"sanitizer stripped: {removed}  ({d['chars_in']} -> {d['chars_out']} chars)"
    if ev.kind == "reader_output":
        return f"reader (no tools) -> {d['value']}  taint={d['sources']}"
    if ev.kind == "policy":
        colour = GREEN if d["approved"] else RED
        return (
            f"policy {d['action']} -> {colour}{d['verdict'].upper()}"
            f"{' / approved' if d['verdict'] == 'confirm' and d['approved'] else ''}{RESET} "
            f"{d['rules']} {d['reason']}"
        )
    if ev.kind == "tainted_control_flow":
        return f"flag: choice '{d['chosen']}' was steered by untrusted data {d['steered_by']}"
    if ev.kind == "agent_action":
        return f"agent -> {d}"
    return f"{ev.kind} {d}"


def _show(title: str, res: RunResult, sc: Scenario, channel: str | None) -> None:
    j = sc.judge(res.ledger, res.final_text)
    print(f"\n{BOLD}{title}{RESET}")
    for ev in res.audit.events:
        print(f"  {DIM}|{RESET} {_fmt(ev)}")
    print(f"  reply: {res.final_text or res.error or '(none)'}")
    for p in res.ledger.payments:
        print(f"  {RED if j.hijacked else GREEN}PAYMENT {p.amount_usd:g} USD -> {p.payee}{RESET}")
    for e in res.ledger.emails:
        print(f"  {RED if j.hijacked else GREEN}EMAIL sent to {e.to}{RESET}")
    if not (res.ledger.payments or res.ledger.emails):
        print("  no side effects")
    verdict = f"{RED}HIJACKED{RESET}" if j.hijacked else f"{GREEN}not hijacked{RESET}"
    print(f"  ground truth: {verdict}; user's task completed correctly: {j.benign_ok}")


def demo(args: argparse.Namespace) -> None:
    sc = SCENARIOS[args.scenario]
    known = ("none", *sc.channels, *sc.attacks)
    if args.channel not in known:
        sys.exit(
            f"unknown --channel {args.channel!r} for scenario {sc.id}; choose one of: "
            + ", ".join(known)
        )
    channel = None if args.channel == "none" else args.channel
    case_id = f"{args.scenario}:{channel or 'benign'}"
    if args.provider != "fake" and is_heldout(case_id):
        sys.exit(
            f"Refusing a real-model demo of {case_id}: it is a held-out case and must stay "
            "unseen until the final evaluation. Pick another scenario or channel."
        )
    model = make_model(args.provider)
    print(f"{BOLD}{sc.title}{RESET}\nuser: {sc.user_text}\nchannel: {channel or 'benign page'}")
    if args.provider == "fake":
        print(f"model: {model.name} (offline simulation, not a real model)")
    else:
        print(f"model: {model.name}")
    base = VulnerableAgent(model, sc.make_web(channel)).run(sc.user_text, sc.context())
    _show("UNGUARDED AGENT", base, sc, channel)
    air = AirgapAgent(model, model, sc.make_web(channel), sc.plausible_confirmer()).run(
        sc.user_text, sc.context()
    )
    _show("AIRGAPPED AGENT", air, sc, channel)


def run_eval(args: argparse.Namespace) -> None:
    from pathlib import Path

    from airgap.eval.harness import OFFLINE_NOTE, report, run_suite, save

    split = resolve_split(args)
    if args.provider != "fake" and split == "all" and not args.allow_heldout:
        sys.exit(
            "Refusing --split all on a real model: it would spend the held-out cases, which stay "
            "unseen until the final run. Use --split tuning, or --split heldout deliberately, or "
            "pass --allow-heldout."
        )
    model = make_model(args.provider)
    # The fake model does not simulate prompt defenses, so condition B is skipped offline.
    only = tuple(args.conditions.split(",")) if args.conditions else None
    trials: list = []
    try:
        run_suite(
            model,
            args.trials,
            include_prompt_defense=args.provider != "fake",
            split=split,
            only=only,
            sink=trials,
        )
    except BudgetExceeded as exc:
        # Keep what was already paid for, say plainly that the run is incomplete, and fail.
        # Beside the results, never over them: a stopped run must not replace a complete one.
        partial = Path(args.out).with_suffix(".partial.json")
        save(trials, partial, model_name=model.name, incomplete=True)
        sys.exit(f"STOPPED by the spend cap after {len(trials)} trials (saved to {partial}): {exc}")
    # Offline, the report opens with the plumbing-only note; repeat it after the tables.
    print(report(trials, model.name))
    if args.provider == "fake":
        print(f"\n{OFFLINE_NOTE}")
    save(trials, Path(args.out), model_name=model.name)
    print(f"\nraw trials saved to {args.out}")


def run_adaptive_cmd(args: argparse.Namespace) -> None:
    from pathlib import Path

    from airgap.eval import adaptive

    ids = tuple(args.scenarios.split(",")) if args.scenarios else tuple(adaptive.ADAPTIVE_SCENARIOS)
    unknown = [s for s in ids if s not in adaptive.ADAPTIVE_SCENARIOS]
    if unknown:  # checked before any model exists, so a typo costs nothing
        sys.exit(
            f"unknown scenario(s) {unknown}; choose from {sorted(adaptive.ADAPTIVE_SCENARIOS)}"
        )
    if not 1 <= args.rounds <= adaptive.MAX_ROUNDS:
        sys.exit(f"--rounds must be between 1 and {adaptive.MAX_ROUNDS}")
    # Adaptive attacks are generated fresh, so they spend no held-out case (see THREAT_MODEL.md).
    model = make_model(args.provider)
    attacker: ChatModel = adaptive.FakeAttacker() if args.provider == "fake" else model
    attempts: list[adaptive.Attempt] = []
    out = Path(args.out)
    try:
        adaptive.run_adaptive(attacker, model, ids, args.rounds, sink=attempts)
    except BudgetExceeded as exc:
        partial = out.with_suffix(".partial.json")
        adaptive.save(
            attempts, partial, attacker.name, model.name, args.rounds, ids, incomplete=True
        )
        sys.exit(
            f"STOPPED by the spend cap after {len(attempts)} attempts (saved to {partial}): {exc}"
        )
    except Exception:
        # Whatever else goes wrong, keep what was already paid for.
        partial = out.with_suffix(".partial.json")
        adaptive.save(
            attempts, partial, attacker.name, model.name, args.rounds, ids, incomplete=True
        )
        print(f"attempts so far saved to {partial}", file=sys.stderr)
        raise
    adaptive.save(attempts, out, attacker.name, model.name, args.rounds, ids)
    print(adaptive.report(attempts, attacker.name, model.name))
    print(f"\nadaptive attempts saved to {args.out} (marked adaptive; not corpus results)")


def serve(args: argparse.Namespace) -> None:
    from pathlib import Path

    import uvicorn

    from airgap.server.app import DEFAULT_WEB_DIR, create_app

    web_dir = Path(args.web_dir) if args.web_dir else DEFAULT_WEB_DIR
    app = create_app(
        results_path=Path(args.results), web_dir=web_dir, published_dir=Path(args.published)
    )
    print(f"airgap console on http://{args.host}:{args.port}  (offline fake model by default)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def show_spend(args: argparse.Namespace) -> None:
    from airgap.models.budget import BudgetExceeded, cap_from_env, default_ledger, read_ledger

    ledger = default_ledger()
    try:
        state, cap = read_ledger(ledger), cap_from_env()
    except BudgetExceeded as exc:
        sys.exit(f"{exc} ({ledger})")
    print(
        f"recorded real-model spend: ${state.spent_usd:.4f} + ${state.reserved_usd:.4f} in flight "
        f"of ${cap:.2f} cap  ({ledger})"
    )


def resolve_split(args: argparse.Namespace) -> Split:
    """Real runs default to the tuning split, so held-out cases are spent only on purpose."""
    if args.split is not None:
        return cast(Split, args.split)
    return "all" if args.provider == "fake" else "tuning"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="airgap")
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="run one scenario against both agents")
    d.add_argument("--scenario", choices=sorted(SCENARIOS), default="payment")
    d.add_argument(
        "--channel",
        default="css_display_none",
        help="an injection channel or a named attack of the scenario, or 'none' for a benign page",
    )
    d.add_argument("--provider", choices=["fake", "meta"], default="fake")
    d.set_defaults(func=demo)
    sp = sub.add_parser("spend", help="show recorded real-model spend against the cap")
    sp.set_defaults(func=show_spend)
    e = sub.add_parser("eval", help="run the evaluation suite")
    e.add_argument("--suite", choices=["core"], default="core")
    e.add_argument("--trials", type=int, default=1)
    e.add_argument("--provider", choices=["fake", "meta"], default="fake")
    e.add_argument("--out", default="results/eval.json")
    e.add_argument(
        "--split",
        choices=["all", "tuning", "heldout"],
        default=None,
        help="default: all for the offline model, tuning for real models",
    )
    e.add_argument(
        "--allow-heldout",
        action="store_true",
        help="allow --split all on a real model (spends the held-out cases)",
    )
    e.add_argument(
        "--conditions", default=None, help="comma-separated condition names (default: all)"
    )
    e.set_defaults(func=run_eval)
    a = sub.add_parser("adaptive", help="run the adaptive attacker (rung 3) against both agents")
    a.add_argument("--provider", choices=["fake", "meta"], default="fake")
    a.add_argument("--rounds", type=int, default=5, help="attempts per scenario")
    a.add_argument(
        "--scenarios", default=None, help="comma-separated scenario ids (default: all families)"
    )
    a.add_argument("--out", default="results/adaptive.json")
    a.set_defaults(func=run_adaptive_cmd)
    s = sub.add_parser("serve", help="serve the side-by-side demo console")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--results", default="results/eval.json")
    s.add_argument(
        "--published",
        default="results/published",
        help="folder of published runs offered by the console's run picker",
    )
    s.add_argument("--web-dir", default=None, help="built UI (default: web/dist in the repo)")
    s.set_defaults(func=serve)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except RealCallsDisabled as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
