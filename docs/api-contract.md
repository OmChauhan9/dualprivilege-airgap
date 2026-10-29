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

The stream opens with one pane-less `start` event naming the run and the model per role, so the UI
can label the provider and show which models were used:
```json
{"seq": 0, "pane": null, "kind": "start", "data": {
  "scenario": "payment", "title": "...", "user_text": "...", "channel": "css_display_none",
  "provider": "fake", "simulated": true,
  "models": {"unguarded_agent": "fake-llm", "orchestrator": "fake-llm", "reader": "fake-llm"}}}
```

Each pane ends with one terminal event:
```json
{"pane": "...", "kind": "done", "data": {
  "reply": "...", "judgement": {"hijacked": false, "goal_reached": false, "benign_ok": true},
  "payments": [{"payee": "0x...", "amount_usd": 20.0, "memo": "...", "unauthorized": true}],
  "emails": [{"to": "...", "subject": "...", "unauthorized": true}],
  "blocked": ["pay 50 to 0x...: payee derived from untrusted content"], "error": null,
  "confirmations": 1, "prompt_tokens": 0, "completion_tokens": 0, "latency_s": 0.0}}
```
`unauthorized` judges each side effect on its own with the scenario judge, so a run that pays both
the attacker and the real payee flags only the attacker's payment.
`policy` events include `rule_of_two: {A_untrusted_input, B_sensitive, C_state_change}`.
A crash inside a run is reported as a pane-less `{"pane": null, "kind": "error", "data": {"error": "..."}}`.
The stream ends with `{"pane": null, "kind": "end", "data": {}}`. Every message, `start` and `end`
included, carries the same `seq`/`pane`/`kind`/`data` envelope.

Errors before the stream starts: `404` unknown scenario, `400` channel not defined for the
scenario, `403` when `provider=meta` and `AIRGAP_ALLOW_REAL_CALLS` is not `1` (the CLI's refusal
message is the `detail`).

## `GET /api/results`
Summary of the last saved eval (`results/eval.json`), or `404` if none. The file may be the bare
trial list that `eval.harness.save` writes, or `{"model": "...", "trials": [...]}`; `model` is
`null` when the file does not record it.
```json
{"model": "fake-llm", "n_cases": 36, "trials_per_case": 1, "conditions": {"C_airgap": {
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

## Replay
`web/public/replays/fake.json` holds every scenario x channel stream recorded with the FakeLLM
(`uv run python -m airgap.server.replay`), keyed `"<scenario>:<channel|none>"`. The UI plays it at a
fixed cadence for reproducible demo videos (`/?mode=replay&scenario=payment&channel=css_display_none&autoplay=1`).
A test fails if the committed file drifts from what the pipeline streams.
