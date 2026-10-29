# Contributing

## Workflow
1. Start from an issue. Each issue is a thin vertical slice that works end to end.
2. Branch from `main`: `feat/...`, `fix/...`, `docs/...`, `test/...`.
3. Test first. Write a failing test for the behavior, make it pass, then refactor.
4. Run the local checks (below), open a PR, and let CI gate the merge. Squash-merge.

Commits use conventional prefixes (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `ci:`).

## Local checks
```bash
uv sync
uv run ruff check src tests && uv run ruff format --check src tests
uv run pyright
uv run pytest -q
```

## Ground rules
- The policy engine, not a prompt, is the security boundary (see `docs/adr/0001-...`).
- Attacks run only against the local mock world, on fictional `.test` domains.
- Use the vocabulary in `GLOSSARY.md`.
- Never commit `.env` or any key. Real model calls need `AIRGAP_ALLOW_REAL_CALLS=1`.
- Report results as k/n with intervals, next to benign completion, and name what is not stopped.
