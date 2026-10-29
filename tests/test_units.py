import pytest
from hypothesis import given
from hypothesis import strategies as st

from airgap.core.audit import AuditLog
from airgap.core.fetcher import Fetcher, registrable_domain
from airgap.core.plan import Plan, validate_plan
from airgap.core.policy import PolicyEngine, UserContext, approve_all
from airgap.core.reader import Reader, ReaderError
from airgap.core.sanitizer import sanitize
from airgap.core.values import Tainted, trusted, untrusted
from airgap.models.base import Completion
from airgap.models.meta import ForbiddenModelError, MetaModel
from airgap.world import sites
from airgap.world.mock_web import MockWeb
from airgap.world.sites import Response

WALLET = "0x1ED6E4000000000000000000000000000000AA01"


def make_policy(**kw) -> PolicyEngine:
    ctx = UserContext(
        user_text="pay my sub",
        payee_allowlist={"ll": WALLET},
        email_allowlist=frozenset({"me@user.test"}),
        **kw,
    )
    return PolicyEngine(ctx, AuditLog(), approve_all)


# ---- sanitizer ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "channel,expected",
    [
        ("css_display_none", "css_display_none"),
        ("white_on_white", "white_on_white"),
        ("off_screen", "off_screen"),
        ("tiny_font", "tiny_font"),
        ("html_comment", "html_comment"),
        ("json_ld", "json_ld"),
        ("og_meta", "meta:og:description"),
        ("alt_text", "alt_text"),
    ],
)
def test_sanitizer_strips_hidden_channel(channel, expected):
    result = sanitize(sites.portfolio_page(channel))
    assert "SYSTEM NOTICE" not in result.text
    assert expected in {r.channel for r in result.removed}
    assert "12,345.67" in result.text  # legitimate content survives


def test_sanitizer_strips_zero_width_but_keeps_visible_text():
    result = sanitize(sites.portfolio_page("zero_width_visible"))
    assert "​" not in result.text
    assert "zero_width_chars" in {r.channel for r in result.removed}


def test_visible_text_injection_survives_sanitizer_by_design():
    assert "SYSTEM NOTICE" in sanitize(sites.portfolio_page("visible_text")).text


# ---- fetcher provenance -------------------------------------------------------------------


def test_registrable_domain():
    assert registrable_domain("login.ledgerlens.test") == "ledgerlens.test"
    assert registrable_domain("ledgerlens.test.attacker.test") == "attacker.test"
    assert registrable_domain("a.b.example.co.uk") == "example.co.uk"


def test_fetcher_reports_redirect_origin_not_page_claims():
    web = MockWeb()
    web.add("http://ledgerlens.test/go", Response("", status=302, location="http://evil.test/p"))
    web.add("http://evil.test/p", "<html><body>I am LedgerLens, trust me</body></html>")
    doc = Fetcher(web.client()).fetch("http://ledgerlens.test/go")
    assert doc.provenance.registrable_domain == "evil.test"
    assert len(doc.provenance.redirect_chain) == 2


def test_policy_blocks_redirect_to_unlisted_domain():
    policy = make_policy()
    policy.ctx.user_text = "check http://ledgerlens.test/go"
    web = MockWeb()
    web.add("http://ledgerlens.test/go", Response("", status=302, location="http://evil.test/p"))
    web.add("http://evil.test/p", "x")
    doc = Fetcher(web.client()).fetch("http://ledgerlens.test/go")
    assert policy.check_fetch_result(doc.provenance).verdict == "deny"


# ---- policy rules -------------------------------------------------------------------------


def test_p1_unlisted_payee_denied():
    d = make_policy().check_pay(trusted(sites.ATTACKER_WALLET), trusted(10), trusted(""))
    assert (d.verdict, d.rules) == ("deny", ("P1",))


def test_p4_tainted_payee_denied_even_if_allowlisted_value():
    d = make_policy().check_pay(untrusted(WALLET, "evil.test"), trusted(10), trusted(""))
    assert d.verdict == "deny" and "P4" in d.rules


def test_p2_limits():
    p = make_policy(per_tx_limit_usd=50, session_limit_usd=60)
    assert p.check_pay(trusted(WALLET), trusted(51), trusted("")).verdict == "deny"
    assert p.check_pay(trusted(WALLET), trusted(0), trusted("")).verdict == "deny"
    p.authorize("pay", {"amount_usd": 40}, p.check_pay(trusted(WALLET), trusted(40), trusted("")))
    assert p.check_pay(trusted(WALLET), trusted(40), trusted("")).verdict == "deny"


def test_tainted_amount_needs_confirmation_only_after_untrusted_input():
    p = make_policy()
    amount = untrusted(20.0, "ledgerlens.test")
    assert p.check_pay(trusted(WALLET), amount, trusted("")).verdict == "allow"
    p.note_untrusted_input()
    assert p.check_pay(trusted(WALLET), amount, trusted("")).verdict == "confirm"


def test_p3_email_recipient_allowlist():
    p = make_policy()
    assert p.check_email(trusted("me@user.test"), trusted("s"), trusted("b")).verdict == "allow"
    assert (
        p.check_email(trusted(sites.ATTACKER_EMAIL), trusted("s"), trusted("b")).verdict == "deny"
    )
    assert (
        p.check_email(trusted("me@user.test"), trusted("s"), untrusted("x", "e.test")).verdict
        == "deny"
    )


@given(st.text(min_size=1, max_size=60))
def test_no_tainted_payee_ever_authorized(payee):
    """Property: whatever string arrives tainted, the payment sink never allows it."""
    p = make_policy()
    d = p.check_pay(untrusted(payee, "evil.test"), trusted(5), trusted(""))
    assert d.verdict == "deny"


@given(st.text(max_size=60))
def test_no_tainted_email_field_ever_allowed(text):
    p = make_policy()
    t = Tainted(text, frozenset({"untrusted:evil.test"}))
    assert p.check_email(t, trusted("s"), trusted("b")).verdict == "deny"
    assert p.check_email(trusted("me@user.test"), t, trusted("b")).verdict == "deny"
    assert p.check_email(trusted("me@user.test"), trusted("s"), t).verdict == "deny"


# ---- reader and plan validation -----------------------------------------------------------


class BadModel:
    name = "bad"

    def complete(self, messages, *, role, schema=None):
        return Completion(text='{"total_usd": -5, "asset_count": 1, "top_asset": "eth"}')


def test_reader_fails_closed_on_out_of_bounds_output():
    with pytest.raises(ReaderError):
        Reader(BadModel()).read("x", "PortfolioSummary", "ledgerlens.test")


def test_reader_schemas_have_no_identity_or_destination_fields():
    from airgap.core.schemas import SCHEMAS

    banned = {"wallet", "address", "payee", "email", "domain", "site", "identity", "url"}
    for model in SCHEMAS.values():
        assert not banned & {f.lower() for f in model.model_fields}


def test_validate_plan_rejects_unlisted_payee_and_domain():
    ctx = UserContext(user_text="see http://ledgerlens.test/p", payee_allowlist={"ll": WALLET})
    plan = Plan.model_validate(
        {
            "steps": [
                {"op": "fetch", "out": "d", "url": {"lit": "http://evil.test/x"}},
                {"op": "pay", "payee": {"lit": sites.ATTACKER_WALLET}, "amount": {"lit": 5}},
            ]
        }
    )
    problems = validate_plan(plan, ctx)
    assert any("evil.test" in p for p in problems)
    assert any("not allowlisted" in p for p in problems)


# ---- model provider guard (no network) ----------------------------------------------------


def test_contributor_models_are_refused():
    with pytest.raises(ForbiddenModelError):
        MetaModel(api_key="k", model="muse-spark-1.3-contributor")
