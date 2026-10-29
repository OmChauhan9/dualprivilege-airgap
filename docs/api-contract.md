# API contract: server and web UI

`airgap serve` runs FastAPI and also serves the built UI (`web/dist`) at `/`. The offline `fake`
provider is the default; `meta` requires `AIRGAP_ALLOW_REAL_CALLS=1` (same guard as the CLI).

## `GET /api/scenarios`
```json
[{"id": "payment", "title": "...", "user_text": "...", "channels": ["css_display_none", "..."]}]
```

## `GET /api/run/stream?scenario=<id>&channel=<channel|none>&provider=fake`
Server-sent events. One run executes the unguarded agent, then the airgapped agent, on the same page.
Each message is JSON:
```json
{"seq": 0, "pane": "unguarded" | "airgap", "kind": "<event kind>", "data": {}}
```
Event kinds come from `core/audit.py` and are passed through unchanged:
`agent_action` (unguarded only), `plan`, `fetch`, `sanitize`, `reader_output`, `policy`,
`tainted_control_flow`, `answer`, `error`. Field shapes are exactly the `emit(...)` keyword
arguments in `core/interpreter.py` and `core/policy.py`.

Each pane ends with one terminal event:
```json
{"pane": "...", "kind": "done", "data": {
  "reply": "...", "judgement": {"hijacked": false, "goal_reached": false, "benign_ok": true},
  "payments": [{"payee": "0x...", "amount_usd": 20.0}], "emails": [{"to": "..."}],
  "confirmations": 1, "prompt_tokens": 0, "completion_tokens": 0, "latency_s": 0.0}}
```
`policy` events include `rule_of_two: {A_untrusted_input, B_sensitive, C_state_change}`.
The stream ends with `{"kind": "end"}`.

## `GET /api/results`
Summary of the last saved eval (`results/eval.json`), or `404` if none:
```json
{"model": "fake-llm", "conditions": {"C_airgap": {
  "hijacked": {"k": 1, "n": 32, "lo": 0.01, "hi": 0.16},
  "goal_reached": {}, "benign_completion": {}, "utility_under_attack": {},
  "mean_latency_s": 0.0, "mean_tokens": 0, "confirmations_per_run": 0.0}},
 "leaks": {"C_airgap": ["steering:visible_text"]}}
```

## UI requirements
Two panes (unguarded left, airgapped right) driven by the same stream; a trace timeline per pane;
provenance badge (origin domain from `fetch`); taint chips on typed values; sanitizer diff viewer
from `sanitize.removed`; policy decision cards with rule ids and the Rule of Two state; a red
alert when the ledger shows an unauthorized payment or email; a results panel from `/api/results`
(attack success next to benign completion, always with k/n and the interval); a visible
"offline fake model" badge when `provider=fake`.
