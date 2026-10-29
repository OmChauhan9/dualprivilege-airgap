"""Day-1 gate and end-to-end checks on the mock world, using the offline FakeLLM only."""

import pytest

from airgap.baseline.vulnerable_agent import VulnerableAgent
from airgap.core.airgap import AirgapAgent
from airgap.models.fake import FakeLLM
from airgap.world import sites
from airgap.world.scenarios import SCENARIOS

HIDDEN_CHANNELS = [c for c in sites.CHANNELS if c != "visible_text"]


def run_baseline(scenario_id: str, channel: str | None):
    sc = SCENARIOS[scenario_id]
    res = VulnerableAgent(FakeLLM(), sc.make_web(channel)).run(sc.user_text, sc.context())
    return res, sc.judge(res.ledger, res.final_text)


def run_airgap(scenario_id: str, channel: str | None, confirmer=None):
    """Default confirmer is a plausible user (approves what a dialog lets them verify)."""
    sc = SCENARIOS[scenario_id]
    agent = AirgapAgent(
        FakeLLM(), FakeLLM(), sc.make_web(channel), confirmer or sc.plausible_confirmer()
    )
    res = agent.run(sc.user_text, sc.context())
    return res, sc.judge(res.ledger, res.final_text)


@pytest.mark.parametrize("scenario_id", ["payment", "email", "identity"])
def test_benign_task_works_in_both_agents(scenario_id):
    _, base = run_baseline(scenario_id, None)
    _, air = run_airgap(scenario_id, None)
    assert base.benign_ok and not base.hijacked
    assert air.benign_ok and not air.hijacked


@pytest.mark.parametrize("channel", sites.CHANNELS)
def test_vulnerable_agent_is_hijacked_on_payment(channel):
    """Gate: the exploit reproduces against the undefended agent on every channel."""
    _, j = run_baseline("payment", channel)
    assert j.goal_reached


@pytest.mark.parametrize("scenario_id", ["payment", "email", "identity"])
@pytest.mark.parametrize("channel", sites.CHANNELS)
def test_airgap_blocks_every_channel(scenario_id, channel):
    res, j = run_airgap(scenario_id, channel)
    assert not j.hijacked, res.audit.events
    assert not j.goal_reached


@pytest.mark.parametrize("scenario_id", ["email", "identity"])
def test_vulnerable_agent_is_hijacked_on_other_scenarios(scenario_id):
    _, j = run_baseline(scenario_id, "css_display_none")
    assert j.goal_reached


def test_steering_hidden_metadata_stops_airgap_but_not_baseline():
    _, base = run_baseline("steering", "json_ld")
    res, air = run_airgap("steering", "json_ld")
    assert base.hijacked
    assert not air.hijacked
    assert res.ledger.payments, "benign steering task should still complete"


def test_steering_visible_lie_is_a_known_limitation():
    """A page that lies in visible text is not stoppable by the pipeline; we say so."""
    from airgap.core.policy import deny_all

    _, base = run_baseline("steering", "visible_text")
    _, air_plausible = run_airgap("steering", "visible_text")
    _, air_strict = run_airgap("steering", "visible_text", deny_all)
    assert base.hijacked
    assert air_plausible.hijacked  # the dialog looks fine, so the user approves the lie
    assert not air_strict.hijacked  # deny-all blocks it, at the cost of never paying


def test_orchestrator_never_sees_page_content():
    sc = SCENARIOS["payment"]

    class Spy(FakeLLM):
        seen: list[str] = []

        def complete(self, messages, *, role, schema=None):
            if role == "orchestrator":
                Spy.seen.append(" ".join(m["content"] for m in messages))
            return super().complete(messages, role=role, schema=schema)

    AirgapAgent(Spy(), FakeLLM(), sc.make_web("css_display_none")).run(sc.user_text, sc.context())
    joined = " ".join(Spy.seen)
    assert "SYSTEM NOTICE" not in joined and sites.ATTACKER_WALLET not in joined
