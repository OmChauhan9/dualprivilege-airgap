import pytest

from airgap import cli
from airgap.models.base import RealCallsDisabled


def test_make_model_raises_typed_error_instead_of_exiting(monkeypatch):
    monkeypatch.delenv("AIRGAP_ALLOW_REAL_CALLS", raising=False)
    with pytest.raises(RealCallsDisabled, match="AIRGAP_ALLOW_REAL_CALLS"):
        cli.make_model("meta")


def test_main_turns_the_refusal_into_a_clean_exit_message(monkeypatch):
    monkeypatch.delenv("AIRGAP_ALLOW_REAL_CALLS", raising=False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["demo", "--provider", "meta"])
    assert "AIRGAP_ALLOW_REAL_CALLS" in str(exc.value.code)
