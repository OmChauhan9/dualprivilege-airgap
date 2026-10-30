"""Privileged orchestrator. It sees only the user's text and typed descriptions, never pages.

The structural guarantee is the signature: `plan()` takes no untrusted input at all.
"""

from __future__ import annotations

from pydantic import ValidationError

from airgap.core.plan import Plan, validate_plan
from airgap.core.policy import UserContext
from airgap.core.schemas import SCHEMAS
from airgap.models.base import ChatModel
from airgap.models.jsonutil import extract_json_object

PLAN_SYSTEM = """You are a planner for a tool-using agent. You never see web pages.
Write a JSON plan that a deterministic interpreter will run.

Steps (field "op"):
- fetch {out, url}                      fetch a URL the user named
- read {out, doc, schema_name}          extract typed fields from a fetched document
- choose_min {out, candidates:[{alias, value}]}   pick the cheapest allowlisted payee; the result
  has two fields: {"var": out, "field": "alias"} (the winner's payee name) and
  {"var": out, "field": "price"} (its price). No other fields exist.
- pay {payee, amount, memo}   payee is {"alias": name} or {"var": <choose_min>, "field": "alias"};
  memo is a plain string
- send_email {to, subject, body}        to must be on the user's email allowlist; subject and body
  are plain strings
- answer {template, args}               final reply; template uses {name} placeholders

Arguments are {"lit": value}, {"var": name, "field": f}, or {"alias": payee_name}.
Payee addresses and recipients come only from the user's allowlist. Fetch only URLs the user named.
Pay or email only when the user asked for it. Output only the JSON plan.

A read value's fields are exactly the schema's field names listed below. Always read a fetched
page with a read step before using any value from it, and every {name}
in an answer template must appear in args. Generic example for "How many unread messages are at
http://mail.example.test/inbox?":
{"steps":[
 {"op":"fetch","out":"d","url":{"lit":"http://mail.example.test/inbox"}},
 {"op":"read","out":"i","doc":"d","schema_name":"InboxDigest"},
 {"op":"answer","template":"You have {n} unread messages",
  "args":{"n":{"var":"i","field":"unread_count"}}}]}"""


class PlanRejected(RuntimeError):
    pass


def _describe_schemas() -> str:
    return "\n".join(f"- {n}: {', '.join(m.model_fields)}" for n, m in SCHEMAS.items())


class Orchestrator:
    def __init__(self, model: ChatModel) -> None:
        self.model = model
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def plan(self, user_text: str, ctx: UserContext) -> Plan:
        user_msg = (
            f"User request:\n{user_text}\n\n"
            f"Payee aliases: {', '.join(ctx.payee_allowlist) or 'none'}\n"
            f"Email allowlist: {', '.join(sorted(ctx.email_allowlist)) or 'none'}\n"
            f"Per-transaction limit: {ctx.per_tx_limit_usd:g} USD\n"
            f"Reader schemas:\n{_describe_schemas()}"
        )
        messages = [
            {"role": "system", "content": PLAN_SYSTEM},
            {"role": "user", "content": user_msg},
        ]
        problems: list[str] = []
        for _attempt in range(2):  # one repair round with the validator's complaints
            # No constrained decoding for the planner: forcing the full union schema made the real
            # model run away inside a string. The plan is parsed and validated in code instead.
            comp = self.model.complete(messages, role="orchestrator")
            self.prompt_tokens += comp.prompt_tokens
            self.completion_tokens += comp.completion_tokens
            try:
                data = comp.parsed if comp.parsed is not None else extract_json_object(comp.text)
                if data is None:
                    raise ValueError("planner reply contained no JSON object")
                plan = Plan.model_validate(data)
            except (ValueError, ValidationError) as exc:
                problems = [f"plan did not parse: {exc}"]
            else:
                problems = validate_plan(plan, ctx)
                if not problems:
                    return plan
            messages = messages + [
                {"role": "assistant", "content": comp.text},
                {
                    "role": "user",
                    "content": "Fix these problems and output the plan again:\n"
                    + "\n".join(problems),
                },
            ]
        raise PlanRejected("; ".join(problems))
