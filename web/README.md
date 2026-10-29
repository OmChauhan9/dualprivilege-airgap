# Demo console

React + Vite + TypeScript + Tailwind. Runs the unguarded agent and the airgapped agent side by side
on the same poisoned page and streams each pane's trace. Interface: `docs/api-contract.md`.

```bash
npm ci && npx vite build          # builds web/dist
cd .. && uv run airgap serve      # http://127.0.0.1:8000, offline fake model by default
```

During UI work, `npm run dev` serves on :5173 and proxies `/api` to `airgap serve` on :8000.

## Replay mode

Switch **Source** to *Replay* to play stored FakeLLM events at a fixed cadence instead of calling
the server. The same URL always plays the same frames in the same order, which keeps a demo video
reproducible:

```
/?mode=replay&scenario=payment&channel=css_display_none&speed=1&autoplay=1
```

*Export trace* saves the current run; *Load trace* replays a saved file. After a pipeline change,
regenerate the bundled recordings with `uv run python -m airgap.server.replay` (a test enforces it).

Everything shown with the fake model is a plumbing check, not evidence about real models.
