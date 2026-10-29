## What and why

<!-- One paragraph. Link the issue: Closes #N -->

## How it was verified

- [ ] `uv run ruff check src tests && uv run ruff format --check src tests`
- [ ] `uv run pyright`
- [ ] `uv run pytest -q`
- [ ] Behavior changes have a test that failed before the change

## Security and honesty checklist

- [ ] No secrets, keys or real brand/site names added
- [ ] No new path where untrusted text reaches the orchestrator or a sink without the policy engine
- [ ] Any claim about results states N, k/n and what is still not stopped
- [ ] Offline `FakeLLM` results are labeled as plumbing checks, not evidence about real models
