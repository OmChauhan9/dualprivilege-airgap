"""Command line: `airgap demo`. (eval and serve are added in later rungs.)"""

from __future__ import annotations

import argparse
import os
import sys

from airgap.baseline.vulnerable_agent import VulnerableAgent
from airgap.core.airgap import AirgapAgent
from airgap.core.audit import Event
from airgap.core.result import RunResult
from airgap.models.base import ChatModel, RealCallsDisabled
from airgap.models.fake import FakeLLM
from airgap.world.scenarios import SCENARIOS, Scenario

RED, GREEN, DIM, BOLD, RESET = "\033[31m", "\033[32m", "\033[2m", "\033[1m", "\033[0m"


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
    channel = None if args.channel == "none" else args.channel
    model = make_model(args.provider)
    print(f"{BOLD}{sc.title}{RESET}\nuser: {sc.user_text}\nchannel: {channel or 'benign page'}")
    base = VulnerableAgent(model, sc.make_web(channel)).run(sc.user_text, sc.context())
    _show("UNGUARDED AGENT", base, sc, channel)
    air = AirgapAgent(model, model, sc.make_web(channel), sc.plausible_confirmer()).run(
        sc.user_text, sc.context()
    )
    _show("AIRGAPPED AGENT", air, sc, channel)


def run_eval(args: argparse.Namespace) -> None:
    from pathlib import Path

    from airgap.eval.harness import OFFLINE_NOTE, report, run_suite, save

    model = make_model(args.provider)
    # The fake model does not simulate prompt defenses, so condition B is skipped offline.
    trials = run_suite(model, args.trials, include_prompt_defense=args.provider != "fake")
    # Offline, the report opens with the plumbing-only note; repeat it after the tables.
    print(report(trials, model.name))
    if args.provider == "fake":
        print(f"\n{OFFLINE_NOTE}")
    save(trials, Path(args.out), model_name=model.name)
    print(f"\nraw trials saved to {args.out}")


def serve(args: argparse.Namespace) -> None:
    from pathlib import Path

    import uvicorn

    from airgap.server.app import DEFAULT_WEB_DIR, create_app

    web_dir = Path(args.web_dir) if args.web_dir else DEFAULT_WEB_DIR
    app = create_app(results_path=Path(args.results), web_dir=web_dir)
    print(f"airgap console on http://{args.host}:{args.port}  (offline fake model by default)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def show_spend(args: argparse.Namespace) -> None:
    import json

    from airgap.models.budget import DEFAULT_LEDGER, cap_from_env

    spent = 0.0
    if DEFAULT_LEDGER.exists():
        spent = float(json.loads(DEFAULT_LEDGER.read_text())["spent_usd"])
    print(f"recorded real-model spend: ${spent:.4f} of ${cap_from_env():.2f} cap")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="airgap")
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="run one scenario against both agents")
    d.add_argument("--scenario", choices=sorted(SCENARIOS), default="payment")
    d.add_argument("--channel", default="css_display_none")
    d.add_argument("--provider", choices=["fake", "meta"], default="fake")
    d.set_defaults(func=demo)
    sp = sub.add_parser("spend", help="show recorded real-model spend against the cap")
    sp.set_defaults(func=show_spend)
    e = sub.add_parser("eval", help="run the evaluation suite")
    e.add_argument("--suite", choices=["core"], default="core")
    e.add_argument("--trials", type=int, default=1)
    e.add_argument("--provider", choices=["fake", "meta"], default="fake")
    e.add_argument("--out", default="results/eval.json")
    e.set_defaults(func=run_eval)
    s = sub.add_parser("serve", help="serve the side-by-side demo console")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--results", default="results/eval.json")
    s.add_argument("--web-dir", default=None, help="built UI (default: web/dist in the repo)")
    s.set_defaults(func=serve)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except RealCallsDisabled as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
