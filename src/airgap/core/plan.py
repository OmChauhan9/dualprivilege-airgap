"""Plan DSL. The orchestrator emits this JSON; code executes it. No native tool calls."""

from __future__ import annotations

import string
from typing import Annotated, Any, Literal, get_args, get_origin

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

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


def _unwrap_lit(value: Any) -> Any:
    """The planner often writes a text field as {"lit": "..."}, like every other argument.

    Accept exactly that shape and nothing else, so a variable reference (which could carry page
    data) is still rejected for these fields.
    """
    if isinstance(value, dict) and set(value) == {"lit"} and isinstance(value["lit"], str):
        return value["lit"]
    return value


# Planner-written text (memo, email subject and body): always a plain string in the plan.
PlanText = Annotated[str, BeforeValidator(_unwrap_lit)]


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
    memo: PlanText = ""


class SendEmail(_S):
    op: Literal["send_email"]
    to: Arg
    subject: PlanText
    body: PlanText


class Answer(_S):
    op: Literal["answer"]
    template: str
    args: dict[str, Arg] = Field(default_factory=dict)


Step = Annotated[Fetch | Read | ChooseMin | Pay | SendEmail | Answer, Field(discriminator="op")]


class Plan(_S):
    steps: list[Step] = Field(max_length=20)


# What the interpreter exposes on a choose_min result (see Interpreter.resolve).
CHOICE_FIELDS = frozenset({"alias", "price"})


# A value of each type a read field can hold, to try a template's format spec on.
_SAMPLE_OF_TYPE: dict[Any, Any] = {float: 1.0, int: 1, bool: True, str: ""}


def validate_plan(plan: Plan, ctx: UserContext) -> list[str]:
    """Static pre-execution check (CaMeLoT-lite). Returns violations; empty means OK."""
    problems: list[str] = []
    kinds: dict[str, str] = {}  # variable -> doc | typed | choice
    schema_of: dict[str, str] = {}  # typed variable -> reader schema name
    price_samples: dict[str, tuple[Any, ...] | None] = {}  # choice variable -> samples(price)

    def check_field(arg: Var, where: str) -> None:
        kind = kinds[arg.var]
        if arg.field is None:
            if kind == "choice":
                problems.append(
                    f"{where}: {arg.var} is a choose_min result; name one of its fields "
                    f"{sorted(CHOICE_FIELDS)} (alias or price)"
                )
            return
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

    def check_number(arg: Arg, what: str, where: str) -> None:
        if isinstance(arg, Lit) and isinstance(arg.lit, bool):
            problems.append(f"{where}: {what} must be a number, not {arg.lit}")

    def samples(arg: Arg) -> tuple[Any, ...] | None:
        """Values of the types `arg` resolves to at run time, or None when that is unknown."""
        if isinstance(arg, Lit):
            return (arg.lit,)
        if isinstance(arg, Alias):
            return ("",)
        kind = kinds.get(arg.var)
        if kind == "choice" and arg.field == "alias":
            return ("",)
        if kind == "choice" and arg.field == "price":
            return price_samples.get(arg.var)
        if kind == "typed" and arg.field and schema_of[arg.var] in SCHEMAS:
            info = SCHEMAS[schema_of[arg.var]].model_fields.get(arg.field)
            if info is not None and get_origin(info.annotation) is Literal:
                return get_args(info.annotation)
            if info is not None and info.annotation in _SAMPLE_OF_TYPE:
                return (_SAMPLE_OF_TYPE[info.annotation],)
        return None

    def check_template(step: Answer, where: str, bad_args: set[str]) -> None:
        """Reject what Interpreter._answer's format_map would raise on, after earlier steps ran."""
        try:
            fields = [
                (name, spec or "", conv)
                for _, name, spec, conv in string.Formatter().parse(step.template)
                if name is not None
            ]
        except ValueError as exc:
            problems.append(
                f"{where}: template is not a valid format string ({exc}); write each "
                "placeholder as {name} and a literal brace as {{ or }}"
            )
            return
        for name, spec, conv in fields:
            shown = "{" + name + (f"!{conv}" if conv else "") + (f":{spec}" if spec else "") + "}"
            if not name.isidentifier():
                problems.append(
                    f"{where}: template placeholder {shown} must be a name from args, like {{amt}}"
                )
            elif name not in step.args:
                problems.append(f"{where}: template placeholder {{{name}}} has no entry in args")
            elif conv or "{" in spec:
                problems.append(
                    f"{where}: template placeholder {shown} may not use a !conversion or a "
                    "nested {} in its format spec"
                )
            elif spec and name not in bad_args:
                values = samples(step.args[name])
                if values is None:
                    problems.append(
                        f"{where}: template placeholder {shown} has a format spec, but {name} "
                        "is not a number or text value"
                    )
                    continue
                for value in values:
                    try:
                        format(value, spec)
                    except (TypeError, ValueError):
                        problems.append(
                            f"{where}: template placeholder {shown}: format spec {spec!r} does "
                            f"not fit {name}, which is a {type(value).__name__}"
                        )
                        break

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
                check_number(cand.value, f"candidate {cand.alias} price", where)
                if cand.alias not in ctx.payee_allowlist:
                    problems.append(f"{where}: candidate {cand.alias} is not on the allowlist")
            prices = [samples(cand.value) for cand in step.candidates]
            price_samples[step.out] = (
                None if None in prices else tuple(v for p in prices if p for v in p)
            )
            kinds[step.out] = "choice"
        elif isinstance(step, Pay):
            check_arg(step.payee, where)
            check_arg(step.amount, where)
            check_number(step.amount, "amount", where)
            if isinstance(step.payee, Lit):
                addr = str(step.payee.lit)
                if addr not in ctx.payee_allowlist.values() and addr not in ctx.user_addresses:
                    problems.append(f"{where}: payee {addr} is not allowlisted or user-supplied")
        elif isinstance(step, SendEmail):
            check_arg(step.to, where)
            if isinstance(step.to, Lit) and ctx.listed_recipient(str(step.to.lit)) is None:
                problems.append(f"{where}: recipient {step.to.lit} is not allowlisted")
        elif isinstance(step, Answer):
            bad_args: set[str] = set()  # already reported; a spec check would only repeat it
            for name, arg in step.args.items():
                before = len(problems)
                check_arg(arg, f"{where} arg {name}")
                if isinstance(arg, Var) and kinds.get(arg.var) == "doc":
                    problems.append(
                        f"{where} arg {name}: {arg.var} is a fetched document; "
                        "read it into a typed value first"
                    )
                if len(problems) > before:
                    bad_args.add(name)
            check_template(step, where, bad_args)
    return problems
