"""`airgap reproduce` re-runs the offline numbers, re-derives the recorded ones and checks the docs.

Every test here is offline. No real model is ever built.
"""

import json
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest

from airgap import cli
from airgap.eval import adaptive, harness, machine, reproduce
from airgap.models.budget import BudgetedModel
from airgap.models.fake import FakeLLM
from airgap.models.meta import MetaModel

ROOT = Path(__file__).parents[1]
MACHINE_FIELDS = {
    "platform",
    "os_version",
    "cpu",
    "python",
    "airgap_version",
    "uv_lock_sha256",
    "packages",
    "git",
}


def _refuse(*_args, **_kwargs):
    raise AssertionError("reproduce must never build a real model")


@pytest.fixture(scope="module")
def repo_run(tmp_path_factory):
    """One run on the repo itself, with every way to build a real model rigged to fail."""
    out = tmp_path_factory.mktemp("repro") / "record.json"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cli, "make_model", _refuse)
        mp.setattr(MetaModel, "__init__", _refuse)
        mp.setattr(MetaModel, "from_env", _refuse)
        mp.setattr(BudgetedModel, "__init__", _refuse)
        with pytest.raises(SystemExit) as exc:
            cli.main(["reproduce", "--out", str(out)])
    return exc.value.code, json.loads(out.read_text())


def _copy_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "results" / "published", root / "results" / "published")
    shutil.copytree(ROOT / "docs", root / "docs")
    replays = root / "web" / "public" / "replays"
    replays.mkdir(parents=True)
    shutil.copy(ROOT / "web" / "public" / "replays" / "fake.json", replays)
    for name in ("README.md", "THREAT_MODEL.md", "PROVENANCE.md", "pyproject.toml", "uv.lock"):
        shutil.copy(ROOT / name, root)
    return root


def test_reproduce_on_the_repo_exits_zero(repo_run):
    code, record = repo_run
    assert code == 0
    assert record["mismatches"] == 0
    assert record["checks"] > 100


def test_reproduce_never_builds_a_real_model(repo_run):
    code, _ = repo_run  # the fixture rigged make_model and both providers to raise
    assert code == 0


def test_the_machine_record_has_every_field(repo_run):
    _, record = repo_run
    assert MACHINE_FIELDS <= set(record["machine"])
    assert set(record["machine"]["git"]) == {"commit", "dirty"}
    assert record["machine"]["packages"]["pytest"]
    assert record["seed"] == "none needed (deterministic)"
    assert record["started_utc"] <= record["finished_utc"]
    assert {s["name"] for s in record["steps"]} == {"offline", "numbers", "not_covered"}
    assert all({"name", "checks", "mismatches"} <= set(s) for s in record["steps"])


def test_a_tampered_result_file_exits_one_and_names_the_number(tmp_path, capsys):
    root = _copy_repo(tmp_path)
    path = root / "results" / "published" / "final_corpus_v2.json"
    data = json.loads(path.read_text())
    clean = next(
        t for t in data["trials"]
        if t["condition"] == "C_airgap" and t["is_attack"] and not t["hijacked"]
    )  # fmt: skip
    clean["hijacked"] = True
    path.write_text(json.dumps(data))

    assert reproduce.run(root, tmp_path / "record.json") == 1
    out = capsys.readouterr().out
    assert "final_corpus_v2/C_airgap/hijacked@ci" in out
    assert "15/210 (7%) [4%, 11%]" in out  # expected
    assert "16/210 (8%) [5%, 12%]" in out  # derived


def test_a_changed_number_in_a_doc_exits_one(tmp_path, capsys):
    root = _copy_repo(tmp_path)
    readme = root / "README.md"
    text = readme.read_text()
    assert "| 15/210 (7%) [4%, 11%] |" in text
    readme.write_text(text.replace("| 15/210 (7%) [4%, 11%] |", "| 14/210 (7%) [4%, 11%] |"))

    assert reproduce.run(root, tmp_path / "record.json") == 1
    out = capsys.readouterr().out
    assert "final_corpus_v2/C_airgap/hijacked@ci" in out
    assert "README.md" in out


def test_a_stale_not_covered_quote_exits_one(tmp_path, capsys):
    root = _copy_repo(tmp_path)
    readme = root / "README.md"
    text = readme.read_text()
    assert "Run the demo in 60 seconds" in text
    readme.write_text(text.replace("Run the demo in 60 seconds", "Run the demo in 90 seconds"))

    assert reproduce.run(root, tmp_path / "record.json") == 1
    out = capsys.readouterr().out
    assert "Run the demo in 60 seconds" in out
    assert "README.md" in out


def test_reproduce_finds_the_checkout_from_a_subdirectory(monkeypatch):
    monkeypatch.chdir(ROOT / "docs" / "adr")
    assert reproduce.find_root(Path.cwd()) == ROOT


def test_a_root_that_is_not_a_checkout_exits_two(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["reproduce", "--root", str(tmp_path), "--out", str(tmp_path / "r.json")])
    assert exc.value.code == 2
    assert "not a checkout" in capsys.readouterr().out
    assert not (tmp_path / "r.json").exists()


def test_a_missing_file_exits_two(tmp_path, capsys):
    root = _copy_repo(tmp_path)
    (root / "results" / "published" / "final_heldout.json").unlink()

    assert reproduce.run(root, tmp_path / "record.json") == 2
    assert "final_heldout.json" in capsys.readouterr().out
    assert not (tmp_path / "record.json").exists()


def test_a_corrupt_file_exits_two(tmp_path):
    root = _copy_repo(tmp_path)
    (root / "results" / "published" / "adaptive_real.json").write_text("{not json")

    assert reproduce.run(root, tmp_path / "record.json") == 2


def test_eval_save_adds_run_meta_and_keeps_every_old_field(tmp_path):
    trials = harness.run_suite(FakeLLM(), 1, split="heldout", only=("C_airgap",))
    path = tmp_path / "eval.json"
    harness.save(trials, path, model_name="fake-llm", incomplete=True)

    saved = json.loads(path.read_text())
    assert set(saved) == {"model", "trials", "incomplete", "run_meta"}
    assert saved["model"] == "fake-llm" and saved["incomplete"] is True
    assert saved["trials"] == json.loads(json.dumps([asdict(t) for t in trials]))
    assert MACHINE_FIELDS <= set(saved["run_meta"])
    assert saved["run_meta"]["saved_utc"]


def test_adaptive_save_adds_run_meta_and_keeps_every_old_field(tmp_path):
    attempts = adaptive.run_adaptive(adaptive.FakeAttacker(), FakeLLM(), ("payment",), 1)
    path = tmp_path / "adaptive.json"
    adaptive.save(attempts, path, "fake-attacker", "fake-llm", 1, ("payment",))

    saved = json.loads(path.read_text())
    assert set(saved) == {
        "kind", "note", "attacker_model", "target_model", "rounds", "scenarios", "conditions",
        "summary", "attempts", "run_meta",
    }  # fmt: skip
    assert saved["kind"] == "adaptive" and saved["rounds"] == 1
    assert saved["attempts"] == json.loads(json.dumps([a.record() for a in attempts]))
    assert MACHINE_FIELDS <= set(saved["run_meta"])


def _broken_machine(*_args, **_kwargs):
    raise machine.metadata.PackageNotFoundError("dualprivilege-airgap")


def test_eval_save_still_writes_the_results_when_machine_details_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(machine, "machine", _broken_machine)
    trials = harness.run_suite(FakeLLM(), 1, split="heldout", only=("C_airgap",))
    path = tmp_path / "eval.json"
    harness.save(trials, path, model_name="fake-llm")

    saved = json.loads(path.read_text())
    assert saved["model"] == "fake-llm"
    assert saved["trials"] == json.loads(json.dumps([asdict(t) for t in trials]))
    assert saved["run_meta"] == {"error": "PackageNotFoundError"}


def test_adaptive_save_still_writes_the_results_when_machine_details_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(machine, "machine", _broken_machine)
    attempts = adaptive.run_adaptive(adaptive.FakeAttacker(), FakeLLM(), ("payment",), 1)
    path = tmp_path / "adaptive.json"
    adaptive.save(attempts, path, "fake-attacker", "fake-llm", 1, ("payment",))

    saved = json.loads(path.read_text())
    assert saved["attempts"] == json.loads(json.dumps([a.record() for a in attempts]))
    assert saved["run_meta"] == {"error": "PackageNotFoundError"}
