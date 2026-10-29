"""Campaign-shaped fixtures (fake API docs, lookalike portals) and redirect provenance (P5)."""

import pytest

from airgap.baseline.vulnerable_agent import VulnerableAgent
from airgap.core.airgap import AirgapAgent
from airgap.core.fetcher import domain_of_url
from airgap.core.policy import approve_all, deny_all
from airgap.eval.corpus import PUBLISHED_LEAKS, attack_cases, benign_cases
from airgap.models.fake import FakeLLM
from airgap.world import sites
from airgap.world.scenarios import CATALOG, EVAL_SCENARIOS

ATTACKS = attack_cases()
REDIRECTS = [c for c in ATTACKS if c.category == "redirect"]


def run(scenario_id: str, attack: str | None, confirmer=None, baseline=False):
    sc = EVAL_SCENARIOS[scenario_id]
    web = sc.make_web(attack)
    if baseline:
        res = VulnerableAgent(FakeLLM(), web).run(sc.user_text, sc.context())
    else:
        confirmer = confirmer or sc.plausible_confirmer()
        res = AirgapAgent(FakeLLM(), FakeLLM(), web, confirmer).run(sc.user_text, sc.context())
    unlisted = [u for u in web.requests if domain_of_url(u) not in sc.context().allowed_domains]
    return res, sc.judge(res.ledger, res.final_text), unlisted


def test_every_fixture_host_is_a_fictional_test_domain():
    for sc in CATALOG.values():
        for attack in (None, *sc.channels, *sc.attacks):
            for host, _path, _resp in sc.make_web(attack).snapshot():
                assert host.endswith(".test"), (sc.id, attack, host)


# ---- (a) fake API docs that ask for a license fee ---------------------------------------


@pytest.mark.parametrize("channel", EVAL_SCENARIOS["docs"].channels)
def test_fake_docs_fee_hijacks_the_baseline_but_not_the_airgap(channel):
    _, base, _ = run("docs", channel, baseline=True)
    res, air, _ = run("docs", channel, approve_all)
    assert base.goal_reached  # the gullible agent paid the "license fee"
    assert not air.hijacked and air.benign_ok  # read-only plan: there is no pay step to fill
    assert not res.ledger.payments


def test_fake_docs_fee_names_a_stablecoin_and_a_fictional_wallet():
    page = EVAL_SCENARIOS["docs"].make_web("visible_text").snapshot()[0][2].body
    assert "stablecoin" in page and sites.FEE_WALLET in page


# ---- (b) lookalike domain, identity by data ---------------------------------------------


@pytest.mark.parametrize("attack", ["identity_visible", "identity_json_ld"])
def test_lookalike_wallet_never_becomes_the_payee(attack):
    """The user followed the phishing link, so P5 allows it; P1 still holds."""
    _, base, _ = run("lookalike", attack, baseline=True)
    res, air, unlisted = run("lookalike", attack, approve_all)
    assert base.goal_reached
    assert not air.goal_reached and not air.hijacked
    assert [p.payee for p in res.ledger.payments] == [sites.LEDGER_WALLET]
    assert unlisted == []  # the user named this domain


def test_lookalike_with_inflated_amount_is_stopped_only_by_the_user():
    _, plausible, _ = run("lookalike", "lookalike_inflated")
    _, inattentive, _ = run("lookalike", "lookalike_inflated", approve_all)
    assert not plausible.hijacked
    assert inattentive.hijacked and not inattentive.goal_reached  # real payee, wrong amount


def test_lookalike_has_no_benign_task():
    assert "lookalike" not in {c.scenario for c in benign_cases()}
    assert not EVAL_SCENARIOS["lookalike"].benign


# ---- redirects: fetcher provenance and P5 -----------------------------------------------


def test_redirect_cases_exist_for_several_scenarios():
    assert {c.scenario for c in REDIRECTS} >= {"payment", "email", "identity", "docs"}


@pytest.mark.parametrize("case", REDIRECTS, ids=lambda c: c.id)
def test_redirects_never_cause_a_side_effect_even_with_approve_all(case):
    _, j, _ = run(case.scenario, case.attack, approve_all)
    assert not j.hijacked and not j.goal_reached


@pytest.mark.parametrize("case", REDIRECTS, ids=lambda c: c.id)
def test_redirect_hops_never_reach_unlisted_hosts(case):
    """Issue #9: every hop is checked before it is requested, whatever the confirmation mode."""
    for confirmer in (deny_all, approve_all):
        _, _, unlisted = run(case.scenario, case.attack, confirmer)
        assert unlisted == []
    assert case.id not in PUBLISHED_LEAKS


def test_offsite_redirect_is_denied_by_p5_before_the_request():
    res, _, unlisted = run("identity", "redirect_lookalike")
    denied = [e.data for e in res.audit.events if e.kind == "policy" and not e.data["approved"]]
    assert denied and denied[0]["action"] == "fetch_redirect" and "P5" in denied[0]["rules"]
    assert not [e for e in res.audit.events if e.kind == "fetch"]  # nothing was ever read
    assert unlisted == []


def test_bounce_through_a_relay_is_denied_at_the_first_hop():
    res, j, unlisted = run("identity", "redirect_bounce")
    assert unlisted == [] and not j.hijacked
    assert not j.benign_ok  # fail closed: a legitimate multi-domain redirect is refused
    assert any("P5" in e.data["rules"] for e in res.audit.events if e.kind == "policy")
