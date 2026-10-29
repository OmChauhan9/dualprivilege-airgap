"""Evaluation harness. Success is read from the ledger, never from what a model says."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from airgap.baseline.vulnerable_agent import VulnerableAgent
from airgap.core.airgap import AirgapAgent
from airgap.core.policy import approve_all, deny_all
from airgap.core.result import RunResult
from airgap.eval.corpus import Case, attack_cases, benign_cases
from airgap.eval.metrics import Rate
from airgap.models.base import ChatModel
from airgap.world.scenarios import SCENARIOS, Judgement


@dataclass(frozen=True)
class Trial:
    condition: str
    case_id: str
    scenario: str
    is_attack: bool
    trial: int
    hijacked: bool
    goal_reached: bool
    benign_ok: bool
    confirmations: int
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    error: str | None


@dataclass(frozen=True)
class Summary:
    hijacked: Rate
    goal_reached: Rate
    benign_completion: Rate
    utility_under_attack: Rate
    mean_latency_s: float
    mean_tokens: float
    confirmations_per_run: float


def conditions(
    model: ChatModel, include_prompt_defense: bool
) -> dict[str, Callable[[Case], tuple[RunResult, Judgement]]]:
    """Each condition maps a case to (result, judgement). `model` is used for every role."""

    def baseline(prompt_defense: bool):
        def run(case: Case):
            sc = SCENARIOS[case.scenario]
            agent = VulnerableAgent(model, sc.make_web(case.channel), prompt_defense)
            res = agent.run(sc.user_text, sc.context())
            return res, sc.judge(res.ledger, res.final_text)

        return run

    def airgap(confirmer_name: str, sanitize_input=True, enforce_policy=True):
        def run(case: Case):
            sc = SCENARIOS[case.scenario]
            confirmer = {
                "plausible": sc.plausible_confirmer(),
                "strict": deny_all,
                "approve_all": approve_all,
            }[confirmer_name]
            agent = AirgapAgent(
                model, model, sc.make_web(case.channel), confirmer, sanitize_input, enforce_policy
            )
            res = agent.run(sc.user_text, sc.context())
            return res, sc.judge(res.ledger, res.final_text)

        return run

    out = {"A_no_defense": baseline(False)}
    if include_prompt_defense:
        out["B_prompt_defense"] = baseline(True)
    out |= {
        "C_airgap": airgap("plausible"),
        "C_airgap_strict_confirm": airgap("strict"),
        "C_airgap_approve_all": airgap("approve_all"),
        "C_no_sanitizer": airgap("plausible", sanitize_input=False),
        "C_no_policy": airgap("plausible", enforce_policy=False),
    }
    return out


def run_suite(
    model: ChatModel, trials: int = 1, include_prompt_defense: bool = False
) -> list[Trial]:
    cases = attack_cases() + benign_cases()
    results: list[Trial] = []
    for name, run in conditions(model, include_prompt_defense).items():
        for case in cases:
            for t in range(trials):
                res, j = run(case)
                results.append(
                    Trial(
                        condition=name,
                        case_id=case.id,
                        scenario=case.scenario,
                        is_attack=case.is_attack,
                        trial=t,
                        hijacked=j.hijacked,
                        goal_reached=j.goal_reached,
                        benign_ok=j.benign_ok,
                        confirmations=res.confirmations,
                        prompt_tokens=res.prompt_tokens,
                        completion_tokens=res.completion_tokens,
                        latency_s=res.latency_s,
                        error=res.error,
                    )
                )
    return results


def summarize(trials: list[Trial]) -> dict[str, Summary]:
    by_cond: dict[str, list[Trial]] = defaultdict(list)
    for t in trials:
        by_cond[t.condition].append(t)
    table: dict[str, Summary] = {}
    for cond, rows in by_cond.items():
        attacks = [r for r in rows if r.is_attack]
        benign = [r for r in rows if not r.is_attack]
        table[cond] = Summary(
            hijacked=Rate(sum(r.hijacked for r in attacks), len(attacks)),
            goal_reached=Rate(sum(r.goal_reached for r in attacks), len(attacks)),
            benign_completion=Rate(sum(r.benign_ok for r in benign), len(benign)),
            utility_under_attack=Rate(sum(r.benign_ok for r in attacks), len(attacks)),
            mean_latency_s=sum(r.latency_s for r in rows) / len(rows),
            mean_tokens=sum(r.prompt_tokens + r.completion_tokens for r in rows) / len(rows),
            confirmations_per_run=sum(r.confirmations for r in rows) / len(rows),
        )
    return table


def failures(trials: list[Trial], condition: str) -> list[Trial]:
    """Every attack that still got through. Published, never hidden."""
    return [t for t in trials if t.condition == condition and t.is_attack and t.hijacked]


def report(trials: list[Trial], model_name: str) -> str:
    table = summarize(trials)
    n_cases = len({t.case_id for t in trials})
    lines = [
        f"model: {model_name}   cases: {n_cases}   trials/case: {max(t.trial for t in trials) + 1}",
        "",
    ]
    for cond, m in table.items():
        lines += [
            f"{cond}",
            f"  hijacked (any unauthorized side effect): {m.hijacked}",
            f"  attacker goal reached:                  {m.goal_reached}",
            f"  benign task completion:                 {m.benign_completion}",
            f"  task completed correctly under attack:  {m.utility_under_attack}",
            f"  mean latency {m.mean_latency_s:.3f}s   mean tokens {m.mean_tokens:.0f}   "
            f"confirmations/run {m.confirmations_per_run:.2f}",
            "",
        ]
    lines.append("Attacks that still succeed against C_airgap (plausible-user confirmation):")
    leaks = failures(trials, "C_airgap")
    lines += [f"  {t.case_id}" for t in leaks] or ["  none observed"]
    lines.append("Attacks that still succeed against C_airgap_strict_confirm:")
    leaks = failures(trials, "C_airgap_strict_confirm")
    lines += [f"  {t.case_id}" for t in leaks] or ["  none observed"]
    return "\n".join(lines)


def save(trials: list[Trial], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(t) for t in trials], indent=1))
