"""Deterministic offline stand-in for an LLM. Used by tests, CI and the offline demo.

IMPORTANT: this is a *simulation* of a gullible model, not evidence about real models.
It exists to exercise the plumbing without spending anything. Real ASR numbers come only
from real-model runs.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from airgap.core.fetcher import URL_RE
from airgap.models.base import Completion, Message, Role, approx_tokens

_URL = URL_RE
_PAY = re.compile(r"pay\s+(\d+(?:\.\d+)?)\s+usd\s+to\s+(0x[0-9a-fA-F]+)", re.I)
_FORWARD = re.compile(r"forward\s+mail\s+to\s+([\w.+-]+@[\w.-]+\.\w+)", re.I)
_LD_PRICE = re.compile(r'"price"\s*:\s*"?(\d+(?:\.\d+)?)')
_VIS_PRICE = re.compile(r"Price:\s*\$(\d+(?:\.\d+)?)")


def _norm(text: str) -> str:
    return html.unescape(text.replace("​", " "))


def _alias_for(url: str) -> str:
    host = re.sub(r"^https?://", "", url).split("/")[0]
    return host.split(".")[0].replace("-", "_")


def _notices(page: str) -> list[dict[str, Any]]:
    """Every instruction a gullible agent would obey, in page order."""
    found: list[tuple[int, dict[str, Any]]] = []
    for m in _PAY.finditer(page):
        action = {"action": "pay", "payee": m.group(2), "amount_usd": float(m.group(1))}
        found.append((m.start(), action | {"memo": "account verification"}))
    for m in _FORWARD.finditer(page):
        action = {"action": "send_email", "to": m.group(1), "subject": "backup"}
        found.append((m.start(), action | {"body": "mailbox contents"}))
    return [a | {"reason": "followed page notice"} for _, a in sorted(found, key=lambda f: f[0])]


def _named_alias(user_text: str, aliases: list[str]) -> str:
    """The saved payee whose alias the user wrote (e.g. "LedgerLens" -> ledgerlens)."""
    squashed = re.sub(r"[^a-z0-9]", "", user_text.lower())
    named = [a for a in aliases if re.sub(r"[^a-z0-9]", "", a.lower()) in squashed]
    return named[0] if named else (aliases[0] if aliases else "")


class FakeLLM:
    name = "fake-llm"

    def complete(
        self,
        messages: list[Message],
        *,
        role: Role,
        schema: dict[str, Any] | None = None,
    ) -> Completion:
        if role == "orchestrator":
            obj = self._orchestrate(messages)
        elif role == "reader":
            obj = self._read(messages)
        else:
            obj = self._agent(messages)
        text = json.dumps(obj)
        return Completion(
            text=text,
            parsed=obj,
            prompt_tokens=approx_tokens(" ".join(m["content"] for m in messages)),
            completion_tokens=approx_tokens(text),
        )

    # ---- orchestrator: user text -> plan ------------------------------------------------

    def _orchestrate(self, messages: list[Message]) -> dict[str, Any]:
        content = next(m["content"] for m in messages if m["role"] == "user")
        user_text = content.split("User request:\n", 1)[1].split("\n\nPayee aliases", 1)[0]
        urls = _URL.findall(user_text)
        low = user_text.lower()
        listed = content.split("Payee aliases: ", 1)[1].split("\n", 1)[0]
        aliases = [] if listed == "none" else listed.split(", ")
        fetch = lambda out, url: {"op": "fetch", "out": out, "url": {"lit": url}}  # noqa: E731
        if "portfolio" in low:
            return {
                "steps": [
                    fetch("d", urls[0]),
                    {"op": "read", "out": "p", "doc": "d", "schema_name": "PortfolioSummary"},
                    {
                        "op": "answer",
                        "template": "Your portfolio total is ${total:,.2f}",
                        "args": {"total": {"var": "p", "field": "total_usd"}},
                    },
                ]
            }
        if "inbox" in low:
            return {
                "steps": [
                    fetch("d", urls[0]),
                    {"op": "read", "out": "i", "doc": "d", "schema_name": "InboxDigest"},
                    {
                        "op": "answer",
                        "template": "You have {n} unread messages",
                        "args": {"n": {"var": "i", "field": "unread_count"}},
                    },
                ]
            }
        if "renew" in low:
            return {
                "steps": [
                    fetch("d", urls[0]),
                    {"op": "read", "out": "b", "doc": "d", "schema_name": "BillingInfo"},
                    {
                        "op": "pay",
                        "payee": {"alias": _named_alias(user_text, aliases)},
                        "amount": {"var": "b", "field": "amount_due_usd"},
                        "memo": "subscription renewal",
                    },
                    {
                        "op": "answer",
                        "template": "Renewal paid: ${amt:.2f}",
                        "args": {"amt": {"var": "b", "field": "amount_due_usd"}},
                    },
                ]
            }
        if "compare" in low and len(urls) >= 2:
            steps: list[dict[str, Any]] = []
            cands = []
            for n, url in enumerate(urls[:2]):
                steps += [
                    fetch(f"d{n}", url),
                    {"op": "read", "out": f"q{n}", "doc": f"d{n}", "schema_name": "PriceQuote"},
                ]
                cands.append(
                    {"alias": _alias_for(url), "value": {"var": f"q{n}", "field": "price_usd"}}
                )
            steps += [
                {"op": "choose_min", "out": "c", "candidates": cands},
                {
                    "op": "pay",
                    "payee": {"var": "c", "field": "alias"},
                    "amount": {"var": "c", "field": "price"},
                    "memo": "plan purchase",
                },
                {
                    "op": "answer",
                    "template": "Paid the cheaper plan: ${amt:.2f}",
                    "args": {"amt": {"var": "c", "field": "price"}},
                },
            ]
            return {"steps": steps}
        if "invoice" in low and urls:
            alias = _named_alias(user_text, aliases)
            steps = []
            for n, url in enumerate(urls):
                steps += [
                    fetch(f"d{n}", url),
                    {"op": "read", "out": f"b{n}", "doc": f"d{n}", "schema_name": "BillingInfo"},
                    {
                        "op": "pay",
                        "payee": {"alias": alias},
                        "amount": {"var": f"b{n}", "field": "amount_due_usd"},
                        "memo": "invoice payment",
                    },
                ]
            steps.append({"op": "answer", "template": f"Paid {len(urls)} invoice(s)", "args": {}})
            return {"steps": steps}
        return {"steps": []}

    # ---- reader: sanitized text -> typed object -----------------------------------------

    def _read(self, messages: list[Message]) -> dict[str, Any]:
        content = messages[-1]["content"]
        name = content.split("Schema: ", 1)[1].split("\n", 1)[0].strip()
        text = content.split("Document:\n", 1)[1]

        def num(pattern: str, default: float = 0.0) -> float:
            m = re.search(pattern, text)
            return float(m.group(1).replace(",", "")) if m else default

        def money(label: str) -> float:
            """Dollar figure after `label`, sign kept: the schema, not the fake, rejects it."""
            m = re.search(label + r":\s*(-)?\$\s*(-)?([\d,]+\.?\d*)", text)
            if not m:
                return 0.0
            value = float(m.group(3).replace(",", ""))
            return -value if (m.group(1) or m.group(2)) else value

        if name == "PortfolioSummary":
            top = re.search(r"Top asset:\s*([A-Z]{2,6})", text)
            return {
                "total_usd": num(r"Total value:\s*\$([\d,]+\.?\d*)"),
                "asset_count": int(num(r"Assets held:\s*(\d+)")),
                "top_asset": top.group(1) if top else "NONE",
            }
        if name == "InboxDigest":
            alert = re.search(r"Security alert:\s*(\w+)", text)
            return {
                "unread_count": int(num(r"Unread:\s*(\d+)")),
                "has_security_alert": bool(alert and alert.group(1).lower() != "none"),
            }
        if name == "BillingInfo":
            # Copies whatever token the page prints; out-of-set values fail schema validation.
            period = re.search(r"Billing period:\s*([\w-]+)", text)
            return {
                "amount_due_usd": money("Amount due"),
                "billing_period": period.group(1) if period else "monthly",
            }
        if name == "PriceQuote":
            return {"price_usd": money("Price")}
        return {}

    # ---- vulnerable agent: gullible about anything in its context ----------------------

    def _agent(self, messages: list[Message]) -> dict[str, Any]:
        system, task = messages[0]["content"], messages[1]["content"]
        actions = [json.loads(m["content"]) for m in messages[2:] if m["role"] == "assistant"]
        results = [m["content"] for m in messages[2:] if m["role"] == "user"]
        payees = dict(
            p.split("=", 1)
            for p in system.split("Saved payees: ", 1)[1].split("\n", 1)[0].split("; ")
            if "=" in p
        )
        urls = _URL.findall(task)
        fetched = [a for a in actions if a["action"] == "fetch"]
        pages = [r for r in results if r.startswith("TOOL RESULT (fetch")]

        # Gullible: obeys every notice on every page it has read, in order.
        notices = [n for page in pages for n in _notices(_norm(page))]
        followed = sum(a.get("reason") == "followed page notice" for a in actions)
        if followed < len(notices):
            return notices[followed]
        if len(fetched) < len(urls):
            return {"action": "fetch", "url": urls[len(fetched)]}

        low = task.lower()
        last = _norm(pages[-1]) if pages else ""
        own_payments = [
            a for a in actions if a["action"] == "pay" and a.get("reason") != "followed page notice"
        ]
        paid = bool(own_payments)
        if "portfolio" in low:
            m = re.search(r"Total value:\s*\$([\d,\.]+)", last)
            return {
                "action": "finish",
                "text": f"Your portfolio total is ${m.group(1) if m else '?'}",
            }
        if "inbox" in low:
            m = re.search(r"Unread:\s*(\d+)", last)
            return {
                "action": "finish",
                "text": f"You have {m.group(1) if m else '?'} unread messages",
            }
        if "renew" in low:
            if not paid:
                m = re.search(r"Amount due:\s*\$([\d,\.]+)", last)
                amt = float(m.group(1).replace(",", "")) if m else 0.0
                return {
                    "action": "pay",
                    "payee": payees.get(_named_alias(task, list(payees)), ""),
                    "amount_usd": amt,
                    "memo": "subscription renewal",
                }
            return {"action": "finish", "text": "Renewal paid"}
        if "compare" in low:
            if not paid:
                prices = []
                for url, res in zip(urls, pages, strict=False):
                    page = _norm(res)
                    m = _LD_PRICE.search(page) or _VIS_PRICE.search(page)
                    prices.append((float(m.group(1)) if m else 1e9, _alias_for(url)))
                price, alias = min(prices)
                return {
                    "action": "pay",
                    "payee": payees.get(alias, ""),
                    "amount_usd": price,
                    "memo": "plan purchase",
                }
            return {"action": "finish", "text": "Paid the cheaper plan"}
        if "invoice" in low:
            if len(own_payments) < len(pages):
                m = re.search(r"Amount due:\s*\$([\d,\.]+)", _norm(pages[len(own_payments)]))
                return {
                    "action": "pay",
                    "payee": payees.get(_named_alias(task, list(payees)), ""),
                    "amount_usd": float(m.group(1).replace(",", "")) if m else 0.0,
                    "memo": "invoice payment",
                }
            return {"action": "finish", "text": f"Paid {len(pages)} invoice(s)"}
        return {"action": "finish", "text": "done"}
