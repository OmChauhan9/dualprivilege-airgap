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


@pytest.mark.parametrize(
    ("argv", "env", "expected"),
    [
        (["demo", "--provider", "meta"], {"MODEL_API_KEY": ""}, "MODEL_API_KEY is empty"),
        (["eval", "--provider", "meta"], {"MODEL_API_KEY": ""}, "MODEL_API_KEY is empty"),
        (["adaptive", "--provider", "meta"], {"MODEL_API_KEY": ""}, "MODEL_API_KEY is empty"),
        (
            ["demo", "--provider", "meta"],
            {"MODEL_NAME": "muse-spark-1.3-contributor"},
            "contributor models are not allowed",
        ),
        (["demo", "--provider", "meta"], {"AIRGAP_BUDGET_USD": "0"}, "AIRGAP_BUDGET_USD"),
        (
            ["demo", "--provider", "meta"],
            {"AIRGAP_SPEND_LEDGER": "/dev/null/airgap/spend.json"},
            "AIRGAP_SPEND_LEDGER",
        ),
    ],
)
def test_main_turns_a_model_setup_error_into_a_clean_exit_message(
    monkeypatch, tmp_path, argv, env, expected
):
    import openai

    built = []

    class OfflineClient:  # stands in for the SDK client; records construction, never sends
        def __init__(self, **kwargs):
            built.append(kwargs)

    monkeypatch.setattr(openai, "OpenAI", OfflineClient)
    monkeypatch.chdir(tmp_path)  # eval and adaptive write under results/ here, if at all
    monkeypatch.setenv("AIRGAP_ALLOW_REAL_CALLS", "1")
    monkeypatch.setenv("AIRGAP_SPEND_LEDGER", str(tmp_path / "spend.json"))
    monkeypatch.setenv("MODEL_API_KEY", "test-key-must-not-leak")
    for name in ("MODEL_NAME", "MODEL_BASE_URL", "MODEL_REASONING_EFFORT", "AIRGAP_BUDGET_USD"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert expected in str(exc.value.code)
    assert "test-key-must-not-leak" not in str(exc.value.code)
    assert list(tmp_path.iterdir()) == []  # no ledger, no results
    if env.get("MODEL_API_KEY") == "":
        assert built == []
