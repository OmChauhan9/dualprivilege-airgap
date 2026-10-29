# 1. Plain-code policy engine is the security boundary

Status: accepted

## Context
Prompt-level defenses ("ignore instructions in documents") are probabilistic, and adaptive attacks have repeatedly broken them. Classifiers and guardrail models are detection, not enforcement.

## Decision
Every side effect passes through a deterministic policy engine written as ordinary code. Models propose; code disposes. The sanitizer, reader typing and any classifier reduce attack surface, but none of them is the boundary.

## Consequences
- Security claims can be unit-tested and property-tested (no tainted string reaches a payee sink).
- Utility drops where tasks need reasoning over untrusted text; we report that cost next to the attack success rate.
- Data-driven attacks that stay inside policy (a vendor lying about a price) are not stopped. We name them rather than hide them.
