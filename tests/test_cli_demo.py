"""The demo command refuses a channel its scenario does not define."""

import pytest

from airgap import cli
from airgap.world.scenarios import SCENARIOS


def test_unknown_channel_is_refused_with_the_valid_choices():
    with pytest.raises(SystemExit) as stop:
        cli.main(["demo", "--channel", "nosuch"])
    message = str(stop.value.code)
    assert "unknown --channel 'nosuch'" in message and "css_display_none" in message


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_every_scenario_runs_without_a_channel_flag(scenario, capsys):
    cli.main(["demo", "--scenario", scenario])
    assert "AIRGAPPED AGENT" in capsys.readouterr().out


def test_steering_without_a_channel_flag_runs_the_benign_page(capsys):
    cli.main(["demo", "--scenario", "steering"])
    assert "channel: benign page" in capsys.readouterr().out


def test_a_channel_the_scenario_lacks_is_still_refused():
    with pytest.raises(SystemExit) as stop:
        cli.main(["demo", "--scenario", "steering", "--channel", "css_display_none"])
    assert "unknown --channel 'css_display_none' for scenario steering" in str(stop.value.code)


def test_named_attack_and_none_are_accepted(capsys):
    cli.main(["demo", "--scenario", "steering", "--channel", "price_penny"])
    cli.main(["demo", "--scenario", "email", "--channel", "none"])
    assert "PAYMENT 0.01 USD" in capsys.readouterr().out


def test_offline_demo_says_it_is_a_simulation(capsys):
    cli.main(["demo"])
    assert "model: fake-llm (offline simulation, not a real model)" in capsys.readouterr().out


def test_piped_output_has_no_color_codes():
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-m", "airgap.cli", "demo"], capture_output=True, text=True, check=True
    ).stdout
    assert "\033[" not in out and "HIJACKED" in out
