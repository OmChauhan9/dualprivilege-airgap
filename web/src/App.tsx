import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchScenarios,
  loadReplays,
  parseTrace,
  scenariosFromReplays,
  streamRun,
  type TraceExport,
} from "./api";
import { Brief, Controls, Header, type Mode, ModelsPanel } from "./components/Chrome";
import { Pane, type PaneStatus } from "./components/Pane";
import { ResultsPanel } from "./components/ResultsPanel";
import type { Envelope, PaneId, Provider, Scenario } from "./types";
import { type Speed, usePlayer } from "./usePlayer";

// URL state makes a demo take reproducible: ?mode=replay&scenario=payment&channel=css_display_none&autoplay=1
function readUrl() {
  const q = new URLSearchParams(window.location.search);
  const speed = q.get("speed");
  return {
    mode: (q.get("mode") === "replay" ? "replay" : "live") as Mode,
    scenario: q.get("scenario") ?? "payment",
    channel: q.get("channel") ?? "css_display_none",
    provider: (q.get("provider") === "meta" ? "meta" : "fake") as Provider,
    speed: (speed === "instant" ? "instant" : [0.5, 1, 2, 4].includes(Number(speed)) ? Number(speed) : 1) as Speed,
    autoplay: q.get("autoplay") === "1",
  };
}

const initial = readUrl();

export function App() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [api, setApi] = useState<"up" | "down" | "checking">("checking");
  const [mode, setMode] = useState<Mode>(initial.mode);
  const [scenario, setScenario] = useState(initial.scenario);
  const [channel, setChannel] = useState(initial.channel);
  const [provider, setProvider] = useState<Provider>(initial.provider);
  const [speed, setSpeed] = useState<Speed>(initial.speed);
  const [pending, setPending] = useState(false);
  const [source, setSource] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const autoplayed = useRef(false);
  const { view, push, reset, fail } = usePlayer(speed);

  useEffect(() => {
    fetchScenarios()
      .then((s) => {
        setScenarios(s);
        setApi("up");
      })
      .catch(async () => {
        // No API (for example a static `vite preview`): fall back to the recordings.
        setApi("down");
        setMode("replay");
        try {
          setScenarios(scenariosFromReplays(await loadReplays()));
        } catch {
          setNotice("API offline and no replay recordings found.");
        }
      });
  }, []);

  const sc = scenarios.find((s) => s.id === scenario);
  useEffect(() => {
    if (sc && channel !== "none" && !sc.channels.includes(channel)) setChannel(sc.channels[0] ?? "none");
  }, [sc, channel]);

  useEffect(() => {
    const q = new URLSearchParams({ mode, scenario, channel, speed: String(speed) });
    if (provider !== "fake") q.set("provider", provider);
    window.history.replaceState(null, "", `?${q.toString()}`);
  }, [mode, scenario, channel, provider, speed]);

  const playAll = useCallback(
    (msgs: Envelope[]) => {
      for (const m of msgs) push(m);
      if (!msgs.some((m) => m.kind === "end")) push({ seq: msgs.length, pane: null, kind: "end", data: {} });
    },
    [push],
  );

  const run = useCallback(async () => {
    abort.current?.abort();
    reset();
    setNotice(null);
    setPending(true);
    try {
      if (mode === "live") {
        setSource(`live · GET /api/run/stream · provider=${provider}`);
        const ctl = new AbortController();
        abort.current = ctl;
        await streamRun({ scenario, channel, provider }, push, ctl.signal);
      } else {
        const file = await loadReplays();
        const msgs = file.runs[`${scenario}:${channel}`];
        if (!msgs) throw new Error(`no recording for ${scenario}:${channel}`);
        setSource("replay · replays/fake.json · FakeLLM recording");
        playAll(msgs);
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        const msg = (err as Error).message;
        fail(msg);
        setNotice(msg);
      }
    } finally {
      setPending(false);
    }
  }, [mode, provider, scenario, channel, push, reset, fail, playAll]);

  useEffect(() => {
    if (initial.autoplay && !autoplayed.current && sc) {
      autoplayed.current = true;
      void run();
    }
  }, [sc, run]);

  const loadFile = useCallback(
    async (f: File) => {
      abort.current?.abort();
      reset();
      setNotice(null);
      try {
        const msgs = parseTrace(await f.text());
        setMode("replay");
        const start = msgs.find((m) => m.kind === "start")?.data as { scenario?: string; channel?: string | null } | undefined;
        if (start?.scenario) setScenario(start.scenario);
        if (start) setChannel(start.channel ?? "none");
        setSource(`replay · ${f.name}`);
        playAll(msgs);
      } catch (err) {
        setNotice(`Could not load ${f.name}: ${(err as Error).message}`);
      }
    },
    [reset, playAll],
  );

  const exportTrace = useCallback(() => {
    const body: TraceExport = { format: "airgap-trace/v1", messages: view.received };
    const url = URL.createObjectURL(new Blob([JSON.stringify(body, null, 1)], { type: "application/json" }));
    const a = Object.assign(document.createElement("a"), {
      href: url,
      download: `airgap-trace-${view.start?.scenario ?? scenario}-${view.start?.channel ?? "none"}.json`,
    });
    a.click();
    URL.revokeObjectURL(url);
  }, [view.received, view.start, scenario]);

  const running = pending || (view.start !== null && !view.ended);
  const status = (p: PaneId): PaneStatus =>
    view.panes[p].done || (view.ended && view.start) ? "done" : running ? "running" : "idle";
  const showFake = view.start ? view.start.simulated : mode === "replay" || provider === "fake";

  return (
    <div className="mx-auto max-w-[1680px] space-y-4 px-5 py-5">
      <Header showFake={showFake} mode={mode} api={api} />
      <Controls
        scenarios={scenarios}
        scenario={scenario}
        channel={channel}
        provider={provider}
        mode={mode}
        speed={speed}
        running={running}
        canExport={view.ended && view.received.length > 0}
        onScenario={setScenario}
        onChannel={setChannel}
        onProvider={setProvider}
        onMode={setMode}
        onSpeed={setSpeed}
        onRun={() => void run()}
        onExport={exportTrace}
        onLoadFile={(f) => void loadFile(f)}
      />
      {(notice || view.error) && (
        <div role="alert" className="rounded-lg border border-crit/60 bg-crit/10 px-4 py-2.5 font-mono text-[13px] text-crit">
          {notice ?? view.error}
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-[1fr_380px]">
        <Brief scenario={sc} channel={channel} source={source} />
        <ModelsPanel start={view.start} provider={provider} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Pane id="unguarded" view={view.panes.unguarded} start={view.start} status={status("unguarded")} />
        <Pane id="airgap" view={view.panes.airgap} start={view.start} status={status("airgap")} />
      </div>
      <ResultsPanel />
      <footer className="flex flex-wrap gap-x-6 gap-y-1 pb-4 font-mono text-[11px] text-fg-faint">
        <span>Attacks run only against an in-process mock web on fictional .test domains.</span>
        <span>Hijacked = the ledger shows an unauthorized side effect.</span>
        <span>Apache-2.0</span>
      </footer>
    </div>
  );
}
