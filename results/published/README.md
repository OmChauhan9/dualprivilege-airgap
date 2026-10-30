# Published results

Raw trials and console logs for every real-model run and the offline reference run. Nothing here
has been edited except one thing: the local home directory in the final "spend" line of each log
is replaced with `~`. Real-model runs used `muse-spark-1.3` on Meta's Model API (Standard mode).

Each `.json` holds one record per trial (condition, case id, category, whether the case is in the
held-out split, hijacked / goal reached / benign OK, attacker paid, requests to unlisted domains,
errors, tokens, latency). Re-render a file in the console with
`uv run airgap serve` after copying it to `results/eval.json`.

| File | What it is | Split | Code it ran on |
|---|---|---|---|
| `final_heldout.json` / `.log` | **The final evaluation.** All five conditions, 3 trials per case, run once | held-out (24 cases) | `cf4ac2f` |
| `real_C_tuning_v3.json` / `.log` | Airgap (plausible, strict, approve-all), 1 trial per case | tuning (72 cases) | `84af775` |
| `real_C_tuning_v2.json` / `.log` | Airgap before the `{"lit": ...}` memo fix (15 runs errored across the three modes, all plan-parse failures) | tuning | `b22da4c` |
| `real_BC_tuning.json` / `.log` | Prompt defense and the first airgap run (15 of 72 runs errored, 9 of them plan-parse failures) | tuning | `05f9b34` |
| `real_A_tuning.json` / `.log` | Unguarded baseline | tuning | `05f9b34` |
| `adaptive_real.json` / `.log` | **Adaptive attacker**, `muse-spark-1.3` vs itself, 10 scenarios x 5 rounds, three conditions. Fresh attacker-written pages, **not** corpus/held-out/tuning cases; file is marked `"kind": "adaptive"` and the console refuses to load it as eval results. 11 of 50 rounds were unusable and are listed unscored | none (adaptive) | `0639618` |
| `offline_fakellm.json` | The offline simulated model (`FakeLLM`): a plumbing check, not evidence about real models | all | n/a |
| `invalid/first_run_INVALID.*` | **Invalid, kept so it is not hidden.** The first real run forced JSON-schema decoding, which corrupted key names and made the unguarded agent look hijacked when it had only mis-parsed. Replaced by `real_A_tuning`. | tuning | before `eaa36a7` |

Commits for the tuning runs are inferred from file times and the merge log, so treat them as
approximate. Only `final_heldout.log` (`cf4ac2f`) and `adaptive_real.log` (`0639618`) record their commit,
taken by the wrapper at the end of the run. `offline_fakellm.json` was regenerated with the current code on Oct 30 (the original
file came from older code and included a prompt-defense condition the offline model does not
simulate).

How to read them: the tuning split was used while fixing the planner and the harness, so its numbers
are development numbers. The held-out split was not used for tuning and was run once. See the
README's "Real-model results" section for what each does and does not show.
