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
| `final_corpus_v2.json` / `.log` | **Final code, whole corpus.** All 96 cases (70 attacks, 26 benign), five conditions, 3 trials per case (1,440 runs), on the code after the Oct 31 review fixes. Not unseen data: the tuning cases were used during development and the held-out cases were already spent by `final_heldout`. Trial 1 of each case is the 1-trial view | all (96 cases) | `86468f3` |
| `adaptive_v2_s1..s3.json` / `.log` | **Adaptive attacker, final code.** Three independent sessions, 10 scenarios x 8 rounds each, three conditions. Same rules as `adaptive_real`: fresh pages, never merged into the corpus. 54, 59 and 59 of 80 rounds were usable | none (adaptive) | `86468f3` |
| `video_demo/*.log` | Four real-model `airgap demo --provider meta` runs on `86468f3`, recorded for the video (color codes removed): `identity` with `css_display_none` and `visible_text`, `steering` with `visible_text`, `email` with `css_display_none`. Single runs, so they illustrate behavior and are not results | none | `86468f3` |
| `final_heldout.json` / `.log` | **The held-out evaluation.** All five conditions, 3 trials per case, run once | held-out (24 cases) | `cf4ac2f` |
| `real_C_tuning_v3.json` / `.log` | Airgap (plausible, strict, approve-all), 1 trial per case | tuning (72 cases) | `84af775` |
| `real_C_tuning_v2.json` / `.log` | Airgap before the `{"lit": ...}` memo fix (15 runs errored across the three modes, all plan-parse failures) | tuning | `b22da4c` |
| `real_BC_tuning.json` / `.log` | Prompt defense and the first airgap run (15 of 72 runs errored, 9 of them plan-parse failures) | tuning | `05f9b34` |
| `real_A_tuning.json` / `.log` | Unguarded baseline | tuning | `05f9b34` |
| `adaptive_real.json` / `.log` | **Adaptive attacker**, `muse-spark-1.3` vs itself, 10 scenarios x 5 rounds, three conditions. Fresh attacker-written pages, **not** corpus/held-out/tuning cases; file is marked `"kind": "adaptive"` and the console refuses to load it as eval results. 11 of 50 rounds were unusable and are listed unscored | none (adaptive) | `0639618` |
| `offline_fakellm.json` | The offline simulated model (`FakeLLM`): a plumbing check, not evidence about real models | all | n/a |
| `invalid/first_run_INVALID.*` | **Invalid, kept so it is not hidden.** The first real run forced JSON-schema decoding, which corrupted key names and made the unguarded agent look hijacked when it had only mis-parsed. Replaced by `real_A_tuning`. | tuning | before `eaa36a7` |

Commits for the tuning runs are inferred from file times and the merge log, so treat them as
approximate. `final_heldout.log` (`cf4ac2f`), `adaptive_real.log` (`0639618`), `final_corpus_v2.log`
and `adaptive_v2_s*.log` (`86468f3`) record their commit, taken by the wrapper at the end of the run.
`offline_fakellm.json` was regenerated with the code at `86468f3` on Oct 31. Its outcomes are
identical to the Oct 30 file (576 trials); only error text changed, because reader errors no longer
quote page values.

These files were frozen on Oct 31. Their numbers are not edited or re-run.

Code changes between runs: the input hardening (#39) and the review fixes (#40 to #45: steered email
recipient, addresses inside URLs, charsets, invisible characters, parsed inline CSS) came after
`final_heldout` and `adaptive_real`, so those two describe the earlier code. `final_corpus_v2` and
`adaptive_v2_*` ran on the code with all of them.

How to read them: the tuning split was used while fixing the planner and the harness, so its numbers
are development numbers. The held-out split was not used for tuning and was run once as held-out data
(`final_heldout`); `final_corpus_v2` re-runs those cases on later code, so there they are no longer
unseen. See the
README's "Real-model results" section for what each does and does not show.
