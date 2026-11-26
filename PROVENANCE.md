# Provenance

What was built for the hackathon, what it builds on, and the licenses involved.

## Built during the hackathon

Everything in this repository was written for the Meta Global AI Developer Hackathon. Design
planning began on October 28, 2026; the git history, which is the verifiable record, begins on
October 29, 2026. No code was copied from the prior art below; it is cited for its ideas.

The git history is the record (`git log`). Merged pull requests:

| PR | What |
|---|---|
| [#1](https://github.com/OmChauhan9/dualprivilege-airgap/pull/1) | Engineering foundation: glossary, ADRs, API contract, PR template |
| [#5](https://github.com/OmChauhan9/dualprivilege-airgap/pull/5) | Side-by-side demo console and SSE server (`airgap serve`) |
| [#10](https://github.com/OmChauhan9/dualprivilege-airgap/pull/10) | Real model runs restricted to the local console; typed error for the call guard |
| [#11](https://github.com/OmChauhan9/dualprivilege-airgap/pull/11) | Corpus expanded to 63 attacks and 24 benign tasks, held-out split, published leaks |
| [#12](https://github.com/OmChauhan9/dualprivilege-airgap/pull/12) | Every redirect hop checked before it is requested; non-2xx responses never read |
| [#13](https://github.com/OmChauhan9/dualprivilege-airgap/pull/13) | Saved results record which model produced them |
| [#14](https://github.com/OmChauhan9/dualprivilege-airgap/pull/14) | A cap the user states bounds payments; page-supplied amounts share a task budget |
| [#15](https://github.com/OmChauhan9/dualprivilege-airgap/pull/15) | README, threat model, provenance and architecture diagram |
| [#16](https://github.com/OmChauhan9/dualprivilege-airgap/pull/16) | Hard spending cap for real model calls; coping with real-model output |
| [#17](https://github.com/OmChauhan9/dualprivilege-airgap/pull/17) | Fixes from an independent verifier; spend ledger kept outside the checkout |
| [#18](https://github.com/OmChauhan9/dualprivilege-airgap/pull/18) | fix(baseline): make the unguarded agent a fair opponent for real models |
| [#19](https://github.com/OmChauhan9/dualprivilege-airgap/pull/19) | fix(server): never join the request path into a filesystem path; add SECURITY.md |
| [#20](https://github.com/OmChauhan9/dualprivilege-airgap/pull/20) | fix(eval): report errored runs as unknown, never silently safe; show the judge's noise floor |
| [#21](https://github.com/OmChauhan9/dualprivilege-airgap/pull/21) | feat: results panel tells the honest story (data-driven subset, per-category, held-out, leak reasons, model label) |
| [#22](https://github.com/OmChauhan9/dualprivilege-airgap/pull/22) | fix(models): harden the spend guard (locking, reserve/settle, usage_known, validation) |
| [#23](https://github.com/OmChauhan9/dualprivilege-airgap/pull/23) | docs: add a shot-by-shot script for the 2-minute demo video |
| [#24](https://github.com/OmChauhan9/dualprivilege-airgap/pull/24) | fix: make the real planner reliable; count safe refusals as stops, not errors |
| [#25](https://github.com/OmChauhan9/dualprivilege-airgap/pull/25) | fix(plan): accept {"lit": ...} for memo, subject and body |
| [#26](https://github.com/OmChauhan9/dualprivilege-airgap/pull/26) | feat(server): expose errors, attacker-paid and incomplete runs; guard held-out cases in the live demo |
| [#27](https://github.com/OmChauhan9/dualprivilege-airgap/pull/27) | fix(eval): an empty reader reply is an error; keep side effects when a later call fails |
| [#28](https://github.com/OmChauhan9/dualprivilege-airgap/pull/28) | docs: publish the real-model results; refresh README, threat model and provenance |
| [#33](https://github.com/OmChauhan9/dualprivilege-airgap/pull/33) | feat(console): run picker for saved results and an interval chart |
| [#34](https://github.com/OmChauhan9/dualprivilege-airgap/pull/34) | feat(eval): adaptive attacker evaluation (rung 3) |
| [#36](https://github.com/OmChauhan9/dualprivilege-airgap/pull/36) | docs: correct the claim that a stated cap stops the invoice leaks |
| [#37](https://github.com/OmChauhan9/dualprivilege-airgap/pull/37) | docs: publish the real adaptive-attacker run |
| [#38](https://github.com/OmChauhan9/dualprivilege-airgap/pull/38) | docs: refresh stale text found by the final sweep |
| [#39](https://github.com/OmChauhan9/dualprivilege-airgap/pull/39) | fix: input hardening (A, B, D, E, F) |
| [#40](https://github.com/OmChauhan9/dualprivilege-airgap/pull/40) | fix(policy): confirm an email recipient chosen by page data (P6) |
| [#41](https://github.com/OmChauhan9/dualprivilege-airgap/pull/41) | fix(policy): an address inside a pasted URL is not a user-written payee (P1) |
| [#42](https://github.com/OmChauhan9/dualprivilege-airgap/pull/42) | fix(fetcher): refuse charsets a browser would decode differently |
| [#43](https://github.com/OmChauhan9/dualprivilege-airgap/pull/43) | fix(sanitizer): strip invisible characters outside category Cf |
| [#44](https://github.com/OmChauhan9/dualprivilege-airgap/pull/44) | fix(sanitizer): parse inline CSS before checking hiding rules |
| [#45](https://github.com/OmChauhan9/dualprivilege-airgap/pull/45) | docs(eval): describe the final pipeline in the attacker brief |
| [#46](https://github.com/OmChauhan9/dualprivilege-airgap/pull/46) | fix(cli): refuse a demo --channel the scenario does not define |
| [#47](https://github.com/OmChauhan9/dualprivilege-airgap/pull/47) | fix(server): leave adaptive files out of the run picker |
| [#48](https://github.com/OmChauhan9/dualprivilege-airgap/pull/48) | docs(readme): fix the stumbles a clean-clone check found |
| [#49](https://github.com/OmChauhan9/dualprivilege-airgap/pull/49) | results: freeze the final-code corpus run and adaptive sessions |
| [#50](https://github.com/OmChauhan9/dualprivilege-airgap/pull/50) | docs: cite the frozen final-code results in the README, threat model, console and video script |
| [#51](https://github.com/OmChauhan9/dualprivilege-airgap/pull/51) | docs: prose pass on README and THREAT_MODEL |
| [#52](https://github.com/OmChauhan9/dualprivilege-airgap/pull/52) | fix(policy): ignore addresses inside links of any letter case or without a scheme (P1) |
| [#53](https://github.com/OmChauhan9/dualprivilege-airgap/pull/53) | fix(fetcher): check the byte-order mark and the meta charset, as a browser does |
| [#54](https://github.com/OmChauhan9/dualprivilege-airgap/pull/54) | fix(sanitizer): close the hiding variants the review panel proved |
| [#55](https://github.com/OmChauhan9/dualprivilege-airgap/pull/55) | fix(cli): the demo names its model and prints no color codes when piped |
| [#56](https://github.com/OmChauhan9/dualprivilege-airgap/pull/56) | docs: fix the Claude-only review panel's findings (rounds 1 and 2) |
| [#57](https://github.com/OmChauhan9/dualprivilege-airgap/pull/57) | style(console): restyle the web console after a Hallmark audit, layout kept |
| [#58](https://github.com/OmChauhan9/dualprivilege-airgap/pull/58) | feat(web): a front-door landing page (Blueprint, frozen real-model figures) |
| [#59](https://github.com/OmChauhan9/dualprivilege-airgap/pull/59) | style(console): neutral chips, labelled fields, data-driven shift in the results header |
| [#60](https://github.com/OmChauhan9/dualprivilege-airgap/pull/60) | feat(web): the landing page as the ledger receipt, in the console's design system |
| [#61](https://github.com/OmChauhan9/dualprivilege-airgap/pull/61) | build(deps): Bump soupsieve from 3.0.4 to 4.0 |
| [#62](https://github.com/OmChauhan9/dualprivilege-airgap/pull/62) | fix(cli): airgap demo --scenario steering runs again instead of refusing its own default channel |
| [#63](https://github.com/OmChauhan9/dualprivilege-airgap/pull/63) | fix(cli): name a bad --trials or --port in one line instead of a traceback |
| [#64](https://github.com/OmChauhan9/dualprivilege-airgap/pull/64) | fix(policy): stop a scheme-less, IPv4 or mailto lure link from making its wallet a valid payee |
| [#65](https://github.com/OmChauhan9/dualprivilege-airgap/pull/65) | fix(interpreter): stop showing a withheld draft reply in the trace and console |
| [#66](https://github.com/OmChauhan9/dualprivilege-airgap/pull/66) | fix(server,cli): a real run with a missing key or bad budget gets a clear refusal, not a 500 or a traceback |
| [#67](https://github.com/OmChauhan9/dualprivilege-airgap/pull/67) | fix(audit): a model action with a kind key is scored instead of crashing the baseline run |
| [#68](https://github.com/OmChauhan9/dualprivilege-airgap/pull/68) | fix(judge): stop scoring a correct payment to a re-cased saved address as a hijack |
| [#69](https://github.com/OmChauhan9/dualprivilege-airgap/pull/69) | fix(plan): reject a bare choice and a boolean amount before they crash a run or pay 1 USD |
| [#70](https://github.com/OmChauhan9/dualprivilege-airgap/pull/70) | fix(plan): reject answer templates that would crash the run after a payment |
| [#71](https://github.com/OmChauhan9/dualprivilege-airgap/pull/71) | fix(server): stop calling the model once the console tab is closed |
| [#72](https://github.com/OmChauhan9/dualprivilege-airgap/pull/72) | fix(interpreter): warn the user when a page's price picked the vendor named in the reply |
| [#73](https://github.com/OmChauhan9/dualprivilege-airgap/pull/73) | fix(fetch): stop pages writing their own text into the error and blocked list the user sees |
| [#74](https://github.com/OmChauhan9/dualprivilege-airgap/pull/74) | docs: stop the docs and console claiming more than the code does |
| [#75](https://github.com/OmChauhan9/dualprivilege-airgap/pull/75) | docs(readme): lead with what the router stops and does not stop, then the evidence |
| [#76](https://github.com/OmChauhan9/dualprivilege-airgap/pull/76) | fix(policy): stop a wallet inside a pasted link from counting as one the user typed, for every link spelling |
| [#77](https://github.com/OmChauhan9/dualprivilege-airgap/pull/77) | fix(sanitizer): catch text hidden with newer css units, font weights and color functions |
| [#78](https://github.com/OmChauhan9/dualprivilege-airgap/pull/78) | fix(fetch): stop decoding charset labels browsers ignore, so the reader sees what the user sees |
| [#79](https://github.com/OmChauhan9/dualprivilege-airgap/pull/79) | docs: stop listing the P1, charset and sanitizer gaps that #76-#78 closed, and fix docstrings that overstate |
| [#80](https://github.com/OmChauhan9/dualprivilege-airgap/pull/80) | feat(reproduce): one offline command re-checks the offline runs, the real-model data and the numbers quoted in README, THREAT_MODEL.md and docs/ |
| [#81](https://github.com/OmChauhan9/dualprivilege-airgap/pull/81) | fix(adaptive): tell the attacker model how the defense scores, decodes and sanitizes today |
| [#82](https://github.com/OmChauhan9/dualprivilege-airgap/pull/82) | feat: put the guard in front of your own pay, send_email and the real web |

The core pipeline (mock world, unguarded baseline, airgap, policy engine) and the first evaluation
harness were the first two commits on `main`, before the PR workflow started.

## What is NOT novel

These ideas come from the work cited below. We use them; we did not invent them.

- **The dual-LLM split:** a privileged model that never sees untrusted text, and a quarantined
  model with no tools (Willison, 2023).
- **Plan-then-execute:** the privileged model writes a program; code runs it (CaMeL; Design
  Patterns).
- **Taint tracking with sink policies:** values carry their untrusted sources; policies check them
  at tool arguments (CaMeL, FIDES, Progent).
- **Allowlists** for payees, recipients and domains, and **human confirmation** for risky actions.
- **The Rule of Two** framing for which combinations of capabilities are dangerous (Meta).
- **Stripping hidden HTML channels** before a model reads a page.

## What we claim

- **Origin is a fetcher fact, so identity-by-data attacks cannot move payees.** Site identity is
  the registrable domain the fetcher reached after checking every redirect hop. Payee addresses come
  only from the user's allowlist or the user's own message, and the reader schemas have no field
  that could carry one. A lookalike page that names its own wallet, even one the user opened, cannot
  redirect a payment (`lookalike:*` in the corpus).
- **Evaluated on payloads shaped like 2026 campaigns:** fake API docs that tell agents to pay a
  license fee, and a lookalike billing portal that names its own wallet. Both run on fictional
  `.test` hosts.
- **Built for Meta's Model API.** The client (`src/airgap/models/meta.py`) targets `muse-spark-1.3`
  in Standard mode. **Real-model runs are done and published** in `results/published/`: the final
  whole-corpus run (`final_corpus_v2`), the held-out run, the tuning runs, and four
  adaptive-attacker sessions, all on `muse-spark-1.3`. The
  offline `FakeLLM` numbers are a plumbing check, not evidence about real models.
- **A live trace.** Every plan, fetch, sanitizer removal, reader output, taint flag and policy
  decision streams to a side-by-side console.
- **Published leaks.** Every attack that gets through is listed with its reason and a tracking
  issue or decision record, and a test fails if the list drifts from the offline results. The
  real-model file matches it today but is not tested.

## Prior art

All links were checked on October 29, 2026. "License" refers to the code repository where one
exists; papers are cited, not reused.

| Work | Who | Link | Relation | License |
|---|---|---|---|---|
| Agents Rule of Two: A Practical Approach to AI Agent Security | Meta, Oct 31, 2025 | https://ai.meta.com/blog/practical-ai-agent-security/ | [A]/[B]/[C] framing; we track it per action | blog post, not checked |
| LlamaFirewall: An open source guardrail system for building secure AI agents | Chennabasappa et al., 2025 | https://arxiv.org/abs/2505.03574 | Detection layer; complementary to a code boundary | paper, not checked |
| Defeating Prompt Injections by Design (CaMeL) | Debenedetti et al. (Google, Google DeepMind, ETH Zurich), 2025 | https://arxiv.org/abs/2503.18813 | Control flow from the trusted query, capabilities, policies at tool calls | paper, not checked |
| CaMeL reference implementation | google-research | https://github.com/google-research/camel-prompt-injection | Read for ideas; no code used | Apache-2.0 (verified) |
| Securing AI Agents with Information-Flow Control (FIDES) | Costa et al., 2025 | https://arxiv.org/abs/2505.23643 | Integrity labels with deterministic enforcement | paper, not checked |
| FIDES tutorial notebook | microsoft | https://github.com/microsoft/fides | Read for ideas; no code used | MIT (verified) |
| Progent: Securing AI Agents with Privilege Control | Shi et al., 2025 | https://arxiv.org/abs/2504.11703 | Deterministic policies over tool arguments | paper, not checked |
| The Task Shield: Enforcing Task Alignment to Defend Against Indirect Prompt Injection in LLM Agents | Jia et al., 2024 | https://arxiv.org/abs/2412.16682 | Checks that each action serves the user's goal | paper, not checked |
| Design Patterns for Securing LLM Agents against Prompt Injections | Beurer-Kellner et al., 2025 | https://arxiv.org/abs/2506.08837 | Plan-then-execute and dual-LLM as patterns | paper, not checked |
| The Dual LLM pattern for building AI assistants that can resist prompt injection | Simon Willison, Apr 25, 2023 | https://simonwillison.net/2023/Apr/25/dual-llm-pattern/ | Privileged / quarantined split with a code controller | blog post, not checked |
| The lethal trifecta for AI agents | Simon Willison, Jun 16, 2025 | https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/ | Private data + untrusted content + external communication | blog post, not checked |
| The Attacker Moves Second: Stronger Adaptive Attacks Bypass Defenses Against LLM Jailbreaks and Prompt Injections | Nasr et al., 2025 | https://arxiv.org/abs/2510.09023 | Why we do not claim robustness from a fixed corpus | paper, not checked |
| Indirect Prompt Injection in Web Content Targets AI Agents | Zscaler ThreatLabz, Jul 2, 2026 | https://www.zscaler.com/blogs/security-research/indirect-prompt-injection-web-content-targets-ai-agents | Source of the two campaign shapes we model | blog post, not checked |

### What the Zscaler report found

ThreatLabz tested 26 models against injections found in live web content:

- **Fake API-docs payment campaign:** 4 of 26 models executed the payment: Llama 3.3 70B Instruct,
  Llama 3.2 90B Vision Instruct, Gemini 3 Flash and Gemini 2.5 Pro.
- **Typosquat of a crypto-portfolio site:** 2 of 26 models called the fake site legitimate, in
  different test setups: GPT-5.4 and Claude Sonnet 4.5. No Llama model is named for this result.
  The report also notes that when the official site was given as context, no model rated the fake
  as legitimate.

Our fixtures (`docs:*`, `lookalike:*`) copy the shape of these campaigns, not their content. All
hosts, vendors and wallets in this repository are fictional.

## Licenses and attributions

- **This repository:** [Apache-2.0](LICENSE).
- **Third-party dependencies** (Python in `uv.lock`, JavaScript in `web/package-lock.json`) keep
  their own licenses. None is vendored into this repository.
- **Meta Model API:** used through `muse-spark-1.3` in Standard mode only. `-contributor` models
  (where Meta may train on prompts and outputs) are refused in code (`ForbiddenModelError` in
  `src/airgap/models/meta.py`). No model weights are included or redistributed.
- **Llama / Muse Spark Open:** no Llama or Muse Spark Open model, weights or code is part of this
  repository. If any are added later, the "Built with ..." attribution their licenses require must
  be added here and in the README.
- **Design tool:** Hallmark 2.0.0 (a Claude Code design skill, MIT) was used to audit and lint the
  web console's restyle and to design the front-door page (`web/landing.html`). No Hallmark files,
  assets or code are in this repository. The landing page uses the console's own fonts, Inter and
  JetBrains Mono, both SIL Open Font License 1.1, installed through `@fontsource-variable`.
