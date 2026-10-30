"""Plan DSL. The orchestrator emits this JSON; code executes it. No native tool calls."""

from __future__ import annotations

import string
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from airgap.core.fetcher import domain_of_url
from airgap.core.policy import UserContext
from airgap.core.schemas import SCHEMAS


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Lit(_S):
    lit: str | float | int | bool


class Var(_S):
    var: str
    field: str | None = None


class Alias(_S):
    alias: str  # resolved to an address from the user's allowlist, by code


Arg = Lit | Var | Alias


class Fetch(_S):
    op: Literal["fetch"]
    out: str
    url: Arg


class Read(_S):
    op: Literal["read"]
    out: str
    doc: str
    schema_name: str


class Candidate(_S):
    alias: str
    value: Arg


class ChooseMin(_S):
    op: Literal["choose_min"]
    out: str
    candidates: list[Candidate] = Field(min_length=1, max_length=8)


class Pay(_S):
    op: Literal["pay"]
    payee: Arg
    amount: Arg
    memo: str = ""


class SendEmail(_S):
    op: Literal["send_email"]
    to: Arg
    subject: str
    body: str


class Answer(_S):
    op: Literal["answer"]
    template: str
    args: dict[str, Arg] = Field(default_factory=dict)


Step = Annotated[Fetch | Read | ChooseMin | Pay | SendEmail | Answer, Field(discriminator="op")]


class Plan(_S):
    steps: list[Step] = Field(max_length=20)


# What the interpreter exposes on a choose_min result (see Interpreter.resolve).
CHOICE_FIELDS = frozenset({"alias", "price"})


def _placeholders(template: str) -> list[str]:
    try:
        return [f for _, f, _, _ in string.Formatter().parse(template) if f]
    except ValueError:
        return []


def validate_plan(plan: Plan, ctx: UserContext) -> list[str]:
    """Static pre-execution check (CaMeLoT-lite). Returns violations; empty means OK."""
    problems: list[str] = []
    kinds: dict[str, str] = {}  # variable -> doc | typed | choice
    schema_of: dict[str, str] = {}  # typed variable -> reader schema name

    def check_field(arg: Var, where: str) -> None:
        if arg.field is None:
            return
        kind = kinds[arg.var]
        if kind == "doc":
            problems.append(f"{where}: {arg.var} is a fetched document and has no fields")
        elif kind == "choice" and arg.field not in CHOICE_FIELDS:
            problems.append(
                f"{where}: a choose_min result has fields {sorted(CHOICE_FIELDS)} "
                f"(alias or price), not {arg.field!r}"
            )
        elif kind == "typed" and schema_of.get(arg.var) in SCHEMAS:
            known = list(SCHEMAS[schema_of[arg.var]].model_fields)
            if arg.field not in known:
                problems.append(
                    f"{where}: {schema_of[arg.var]} has fields {known}, not {arg.field!r}"
                )

    def check_arg(arg: Arg, where: str) -> None:
        if isinstance(arg, Var):
            if arg.var not in kinds:
                problems.append(f"{where}: variable {arg.var} used before it is defined")
            else:
                check_field(arg, where)
        elif isinstance(arg, Alias) and arg.alias not in ctx.payee_allowlist:
            problems.append(f"{where}: alias {arg.alias} is not on the user's allowlist")

    for i, step in enumerate(plan.steps):
        where = f"step {i} ({step.op})"
        if isinstance(step, Fetch):
            check_arg(step.url, where)
            if isinstance(step.url, Lit):
                domain = domain_of_url(str(step.url.lit))
                if domain not in ctx.allowed_domains:
                    problems.append(f"{where}: domain {domain} was not named by the user")
            kinds[step.out] = "doc"
        elif isinstance(step, Read):
            if kinds.get(step.doc) != "doc":
                problems.append(f"{where}: {step.doc} is not a fetched document")
            if step.schema_name not in SCHEMAS:
                problems.append(f"{where}: unknown schema {step.schema_name}")
            kinds[step.out] = "typed"
            schema_of[step.out] = step.schema_name
        elif isinstance(step, ChooseMin):
            for cand in step.candidates:
                check_arg(cand.value, where)
                if cand.alias not in ctx.payee_allowlist:
                    problems.append(f"{where}: candidate {cand.alias} is not on the allowlist")
            kinds[step.out] = "choice"
        elif isinstance(step, Pay):
            check_arg(step.payee, where)
            check_arg(step.amount, where)
            if isinstance(step.payee, Lit):
                addr = str(step.payee.lit)
                if addr not in ctx.payee_allowlist.values() and addr not in ctx.user_addresses:
                    problems.append(f"{where}: payee {addr} is not allowlisted or user-supplied")
        elif isinstance(step, SendEmail):
            check_arg(step.to, where)
            if isinstance(step.to, Lit) and str(step.to.lit).lower() not in {
                e.lower() for e in ctx.email_allowlist
            }:
                problems.append(f"{where}: recipient {step.to.lit} is not allowlisted")
        elif isinstance(step, Answer):
            for name, arg in step.args.items():
                check_arg(arg, f"{where} arg {name}")
                if isinstance(arg, Var) and kinds.get(arg.var) == "doc":
                    problems.append(
                        f"{where} arg {name}: {arg.var} is a fetched document; "
                        "read it into a typed value first"
                    )
            for placeholder in _placeholders(step.template):
                if placeholder not in step.args:
                    problems.append(
                        f"{where}: template placeholder {{{placeholder}}} has no entry in args"
                    )
    return problems
