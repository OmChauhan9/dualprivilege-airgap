"""The demo command refuses a channel its scenario does not define."""

import pytest

from airgap import cli


def test_unknown_channel_is_refused_with_the_valid_choices():
    with pytest.raises(SystemExit) as stop:
        cli.main(["demo", "--channel", "nosuch"])
    message = str(stop.value.code)
    assert "unknown --channel 'nosuch'" in message and "css_display_none" in message


def test_named_attack_and_none_are_accepted(capsys):
    cli.main(["demo", "--scenario", "steering", "--channel", "price_penny"])
    cli.main(["demo", "--scenario", "email", "--channel", "none"])
    assert "PAYMENT 0.01 USD" in capsys.readouterr().out


def test_offline_demo_says_it_is_a_simulation(capsys):
    cli.main(["demo"])
    assert "model: fake-llm (offline simulation, not a real model)" in capsys.readouterr().out
