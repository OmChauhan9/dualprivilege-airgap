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


@pytest.mark.parametrize("trials", ["0", "-1"])
def test_eval_rejects_fewer_than_one_trial_with_a_clean_message(tmp_path, trials):
    out = tmp_path / "eval.json"
    with pytest.raises(SystemExit) as exc:
        cli.main(["eval", "--trials", trials, "--out", str(out)])
    assert exc.value.code == "--trials must be at least 1"
    assert not out.exists()


@pytest.mark.parametrize("port", ["0", "-1", "65536"])
def test_serve_rejects_a_port_it_cannot_listen_on_with_a_clean_message(monkeypatch, port):
    import uvicorn

    started = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: started.append(kw))
    with pytest.raises(SystemExit) as exc:
        cli.main(["serve", "--port", port])
    assert exc.value.code == "--port must be between 1 and 65535"
    assert started == []
