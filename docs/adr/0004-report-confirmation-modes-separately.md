# 4. Report every confirmation mode, never one flattering number

Status: accepted

## Context
A tainted amount (for example the price on a billing page) must be confirmed by the user. How well that works depends on how attentive the user is, which an evaluation cannot know.

## Decision
Every evaluation reports three modes side by side: **strict** (deny all), **plausible user** (approves only what the confirmation dialog lets them verify), and **approve all** (inattentive worst case). We also report confirmations per task and publish each attack that still succeeds.

## Consequences
- Headline claims carry both attack success rate and benign completion, with k/n and Wilson intervals. "0 of 40" is shown with its upper bound.
- Strict mode is safe but costs utility; approve-all shows what the policy does without a human.
