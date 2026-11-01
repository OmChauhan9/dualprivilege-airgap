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
  held-out run, the tuning runs, and one small adaptive-attacker run, all on `muse-spark-1.3`. The
  offline `FakeLLM` numbers are a plumbing check, not evidence about real models.
- **A live trace.** Every plan, fetch, sanitizer removal, reader output, taint flag and policy
  decision streams to a side-by-side console.
- **Published leaks.** Every attack that gets through is listed with its reason and a tracking
  issue or decision record, and a test fails if the list drifts from the results.

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
  assets or code are in this repository. The landing page's fonts (Chakra Petch, IBM Plex Sans, IBM
  Plex Mono) are SIL Open Font License 1.1, installed through `@fontsource`.
