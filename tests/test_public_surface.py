"""Structure the build enforces: the public surface, eval-only seams, and the untrusted-field list.

These rules are invisible from any one file, so a test holds them.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import airgap
from airgap.core.audit import UNTRUSTED_FIELDS

SRC = Path(__file__).resolve().parents[1] / "src" / "airgap"
PUBLIC = {
    "Airgap", "Declined", "Confirmation", "Payment", "Email",
    "RunResult", "Action", "Ledger", "AuditLog", "Event", "ChatModel", "Completion",
}  # fmt: skip
EVAL_ONLY = {"run_ablated", "PermissivePolicy", "approve_all"}
# Where the eval-only seams are defined.
DEFINERS = {"policy.py", "airgap.py"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}


def _emitted_kinds(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        n.args[0].value
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "emit"
        and n.args
        and isinstance(n.args[0], ast.Constant)
        and isinstance(n.args[0].value, str)
    }


def test_the_public_surface_is_exactly_this():
    assert set(airgap.__all__) == PUBLIC
    for name in PUBLIC:
        assert getattr(airgap, name) is not None


def test_run_takes_the_request_and_nothing_that_could_disagree_with_it():
    params = inspect.signature(airgap.Airgap.run).parameters
    assert list(params) == ["self", "request", "run_id", "on_event"]


def test_the_guard_takes_no_http_client():
    params = inspect.signature(airgap.Airgap).parameters
    assert not {"client", "http", "web"} & set(params)
    assert "transport" in params and "private_hosts" in params


def test_eval_only_seams_are_imported_only_by_the_eval():
    for path in SRC.rglob("*.py"):
        if "eval" in path.relative_to(SRC).parts or path.name in DEFINERS:
            continue
        assert not (_imports(path) & EVAL_ONLY), path


def test_nothing_builds_an_httpx_client():
    # The guard talks to a transport; an httpx.Client would bring hooks, headers and cookies back.
    for path in SRC.rglob("*.py"):
        assert "httpx.Client(" not in path.read_text(), path


def test_every_event_kind_the_guard_emits_declares_its_untrusted_fields():
    emitted = set().union(*(_emitted_kinds(p) for p in (SRC / "core").rglob("*.py")))
    assert emitted, "the scan found no emit calls"
    assert emitted <= set(UNTRUSTED_FIELDS), emitted - set(UNTRUSTED_FIELDS)
