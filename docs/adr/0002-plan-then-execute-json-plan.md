# 2. The orchestrator emits a JSON plan, not native tool calls

Status: accepted

## Context
The Meta Model API accepts only `tool_choice: "auto"` and does not guarantee that tool arguments validate against their schema. Native tool calls would put tool execution inside the model loop, next to untrusted content.

## Decision
The orchestrator sees only the user's text and typed descriptions and emits a plan in a small DSL (`fetch`, `read`, `choose_min`, `pay`, `send_email`, `answer`). A static validator checks the plan before it runs (allowlisted payees, user-named domains, defined variables), with one repair round. An interpreter executes it.

## Consequences
- The orchestrator never receives page text, so there is nothing for it to be injected with.
- Branching on fetched data is limited to deterministic ops such as `choose_min`, which the interpreter marks as steered (rule P6).
- Tasks that need open-ended reasoning over page content are out of reach for this design.
