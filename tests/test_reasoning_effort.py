"""muse-spark-1.3 spends output tokens thinking; the planner needs a way to think less."""

import airgap.models.meta as meta


class Client:
    class chat:  # noqa: N801
        class completions:  # noqa: N801
            seen: dict = {}

            @staticmethod
            def create(**kw):
                Client.chat.completions.seen = kw

                class R:
                    class _C:
                        class message:  # noqa: N801
                            content = "{}"

                    choices = [_C]
                    usage = None

                return R


def make(effort):
    m = meta.MetaModel.__new__(meta.MetaModel)
    m.name, m.temperature, m.reasoning_effort = "muse-spark-1.3", 0.7, effort
    m.__dict__["_client"] = Client
    return m


def test_reasoning_effort_is_sent_only_when_it_is_set():
    make(None).complete([{"role": "user", "content": "x"}], role="agent")
    assert "reasoning_effort" not in Client.chat.completions.seen
    make("low").complete([{"role": "user", "content": "x"}], role="agent")
    assert Client.chat.completions.seen["reasoning_effort"] == "low"


def test_from_env_reads_the_setting_and_rejects_unknown_values(monkeypatch):
    import pytest

    monkeypatch.setenv("MODEL_API_KEY", "k")
    monkeypatch.setenv("MODEL_REASONING_EFFORT", "low")
    assert meta.MetaModel.from_env().reasoning_effort == "low"
    monkeypatch.setenv("MODEL_REASONING_EFFORT", "turbo")
    with pytest.raises(ValueError, match="reasoning"):
        meta.MetaModel.from_env()
