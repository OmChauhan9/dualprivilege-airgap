import { Fragment, type ReactNode, useEffect, useState } from "react";
import { fetchResults, fetchRuns, type ResultsState } from "../api";
import { CONDITIONS, categoryLabel, pct } from "../labels";
import type { ConditionJson, LeakFlag, LeakJson, RateJson, ResultsJson, RunJson, RunsJson } from "../types";
import * as I from "./icons";

const { Shield: ShieldIcon, ShieldOff: ShieldOffIcon } = I;

type Good = "low" | "high";

const ci = (r: RateJson) => `95% CI ${pct(r.lo)}–${pct(r.hi)}`;
/** Table cells: same Wilson interval, shorter; the table caption and legend say "95%". */
const ciShort = (r: RateJson) => `CI ${pct(r.lo).replace("%", "")}–${pct(r.hi)}`;
const condLabel = (c: string) => CONDITIONS[c]?.label ?? c;

/** The interval as a band on a 0-100% track, with the point estimate marked. Decorative only. */
function Track({ r, good }: { r: RateJson; good: Good }) {
  const mark = good === "low" ? (r.k > 0 ? "bg-crit-mark" : "bg-fg-muted") : "bg-good-mark";
  const point = r.n ? r.k / r.n : 0;
  return (
    <div className="relative mt-1.5 h-1.5 rounded-full bg-ink-700" aria-hidden="true">
      <span
        className={`absolute inset-y-0 rounded-full ${mark} opacity-80`}
        style={{ left: `${r.lo * 100}%`, width: `max(4px, ${(r.hi - r.lo) * 100}%)` }}
      />
      <span
        className="absolute -top-0.5 -bottom-0.5 w-0.5 rounded bg-fg ring-2 ring-ink-900"
        style={{ left: `calc(${point * 100}% - 1px)` }}
      />
    </div>
  );
}

/** k/n first, Wilson interval always beside it. Never a bare percent. */
function RateText({ r, good, size = "md" }: { r: RateJson; good: Good; size?: "md" | "lg" }) {
  const lg = size === "lg";
  if (r.n === 0) {
    return <span className=" text-[12px] text-fg-muted">not recorded</span>;
  }
  const alarm = good === "low" && r.k > 0;
  return (
    <>
      <span
        className={`font-mono font-bold tabular-nums ${lg ? "text-[24px] leading-7" : "text-[14px]"} ${alarm ? "text-crit" : "text-fg"}`}
      >
        {r.k}/{r.n}
      </span>
      <span className={`block font-mono whitespace-nowrap tabular-nums text-fg-muted ${lg ? "text-[12px]" : "text-[11px]"}`}>
        {lg ? ci(r) : ciShort(r)}
      </span>
    </>
  );
}

function RateCell({ r, good, label, tint = "" }: { r: RateJson; good: Good; label: string; tint?: string }) {
  return (
    <td className={`px-2 py-2.5 align-top ${tint}`} title={r.n ? `${label}: ${r.k} of ${r.n}, Wilson ${ci(r)}` : `${label}: not recorded`}>
      <div className="min-w-[84px]">
        <RateText r={r} good={good} />
        {r.n > 0 && <Track r={r} good={good} />}
      </div>
    </td>
  );
}

function Better({ good }: { good: Good }) {
  return (
    <>
      <span aria-hidden="true">{good === "low" ? "▼" : "▲"}</span>
      <span className="sr-only">{good === "low" ? "lower is better" : "higher is better"}</span>
    </>
  );
}

type RunsState = { status: "loading" } | { status: "ok"; data: RunsJson } | { status: "unavailable" };

export function ResultsPanel() {
  const [runs, setRuns] = useState<RunsState>({ status: "loading" });
  const [picked, setPicked] = useState<string | null>(null);
  const [showInvalid, setShowInvalid] = useState(false);
  const [state, setState] = useState<ResultsState>({ status: "loading" });

  useEffect(() => {
    fetchRuns()
      .then((data) => {
        setRuns({ status: "ok", data });
        setPicked(data.default);
        if (data.default === null) setState({ status: "missing", message: "no saved runs" });
      })
      .catch(() => {
        // No run list (an older server): show the local eval the way the console always did.
        setRuns({ status: "unavailable" });
        void fetchResults().then(setState);
      });
  }, []);

  useEffect(() => {
    if (picked === null) return;
    let live = true;
    setState({ status: "loading" });
    void fetchResults(picked).then((s) => live && setState(s));
    return () => {
      live = false;
    };
  }, [picked]);

  const list = runs.status === "ok" ? runs.data.runs : [];
  const run = list.find((r) => r.id === picked) ?? null;
  const toggleInvalid = (on: boolean) => {
    setShowInvalid(on);
    if (!on && run?.invalid && runs.status === "ok") {
      const next = runs.data.default ?? list.find((r) => !r.invalid)?.id ?? null;
      setPicked(next);
      if (next === null) setState({ status: "missing", message: "no saved runs" });
    }
  };
  return (
    <section className="panel overflow-hidden" aria-labelledby="results-h">
      <header className="flex flex-wrap items-center gap-3 border-b border-ink-700 px-4 py-3">
        <I.Flask className="text-lg text-air" />
        <h2 id="results-h" className=" text-[14px] font-bold">
          Evaluation results
        </h2>
        <span className="font-mono text-[12px] text-fg-muted">
          {run ? `GET /api/results?run=${run.id} · ${run.file}` : "GET /api/results · results/eval.json"}
        </span>
        {state.status === "ok" && (
          <span className="ml-auto flex flex-wrap items-center gap-2 text-[12px] text-fg-muted">
            <span className="chip border-ink-500">model {state.data.model ?? "not recorded"}</span>
            <span className="chip border-ink-500">{state.data.n_cases} cases</span>
            <span className="chip border-ink-500">
              {state.data.trials_per_case} trial{state.data.trials_per_case === 1 ? "" : "s"}/case
            </span>
          </span>
        )}
      </header>
      {list.length > 0 && (
        <RunPicker runs={list} run={run} onPick={setPicked} showInvalid={showInvalid} onShowInvalid={toggleInvalid} />
      )}
      {run?.invalid && <InvalidBanner run={run} />}
      {state.status === "loading" && <p className="px-4 py-6 text-fg-muted">loading…</p>}
      {state.status === "missing" && (
        <div className="space-y-2 px-4 py-6">
          <p className="text-fg">{run ? "This run could not be found." : "No saved evaluation yet."}</p>
          <p className="text-[13px] text-fg-muted">
            Run <code className="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-fg">uv run airgap eval</code>, then
            reload. Results are shown as k/n with Wilson intervals, next to benign completion.
          </p>
        </div>
      )}
      {state.status === "error" && (
        <p role="alert" className="px-4 py-6 text-crit">
          Could not load results: {state.message}
        </p>
      )}
      {state.status === "ok" && (
        <ResultsBody data={state.data} file={run?.file ?? "results/eval.json"} invalid={run?.invalid === true} />
      )}
    </section>
  );
}

// ---- run picker ----------------------------------------------------------------------

type RunKind = "real" | "simulated" | "unrecorded" | "invalid";

const runKind = (r: RunJson): RunKind =>
  r.invalid ? "invalid" : r.simulated === true ? "simulated" : r.simulated === false ? "real" : "unrecorded";

const KINDS: Record<RunKind, { group: string; badge: string; tone: string }> = {
  real: { group: "Real model", badge: "Real model", tone: "border-air/60 bg-air/10 text-air" },
  simulated: {
    group: "Offline simulation (plumbing check, not evidence)",
    badge: "Offline simulation · plumbing check",
    tone: "border-warn/60 bg-warn/10 text-warn",
  },
  unrecorded: { group: "Model not recorded", badge: "Model not recorded", tone: "border-warn/60 bg-warn/10 text-warn" },
  invalid: {
    group: "Invalid (kept so it is not hidden)",
    badge: "Invalid run · not a result",
    tone: "border-crit/60 bg-crit/10 text-crit",
  },
};

function runOption(r: RunJson): string {
  if (r.error) return `${r.label} · unreadable`;
  const trials = `${r.trials_per_case} trial${r.trials_per_case === 1 ? "" : "s"}/case`;
  const model = r.model ?? "model not recorded";
  return `${r.label} · ${model} · ${r.n_cases} cases × ${trials}${r.incomplete ? " · incomplete" : ""}`;
}

function KindBadge({ kind }: { kind: RunKind }) {
  const Icon = kind === "real" ? I.Brain : I.Alert;
  return (
    <span className={`chip ${KINDS[kind].tone}`}>
      <Icon aria-hidden="true" />
      {KINDS[kind].badge}
    </span>
  );
}

function RunPicker({
  runs,
  run,
  onPick,
  showInvalid,
  onShowInvalid,
}: {
  runs: RunJson[];
  run: RunJson | null;
  onPick: (id: string) => void;
  showInvalid: boolean;
  onShowInvalid: (on: boolean) => void;
}) {
  const kinds: RunKind[] = showInvalid ? ["real", "simulated", "unrecorded", "invalid"] : ["real", "simulated", "unrecorded"];
  const invalid = runs.filter((r) => r.invalid);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-ink-700 bg-ink-850 px-4 py-2.5">
      <label htmlFor="run-picker" className="label">
        Run
      </label>
      <select
        id="run-picker"
        value={run?.id ?? ""}
        onChange={(e) => onPick(e.target.value)}
        className="max-w-full min-w-0 flex-1 rounded-md border border-edge-field bg-ink-900 px-2 py-1.5 font-mono text-[13px] text-fg sm:flex-none"
      >
        {kinds.map((k) => {
          const rows = runs.filter((r) => runKind(r) === k);
          return (
            rows.length > 0 && (
              <optgroup key={k} label={KINDS[k].group}>
                {rows.map((r) => (
                  <option key={r.id} value={r.id} disabled={r.error !== null}>
                    {runOption(r)}
                  </option>
                ))}
              </optgroup>
            )
          );
        })}
      </select>
      {run && <KindBadge kind={runKind(run)} />}
      {invalid.length > 0 && (
        <label className="ml-auto flex cursor-pointer items-center gap-2 font-mono text-[12px] text-fg-muted hover:text-fg">
          <input
            type="checkbox"
            checked={showInvalid}
            onChange={(e) => onShowInvalid(e.target.checked)}
            className="accent-crit"
          />
          Show invalid run{invalid.length === 1 ? "" : "s"} ({invalid.length})
        </label>
      )}
    </div>
  );
}

function InvalidBanner({ run }: { run: RunJson }) {
  return (
    <div role="note" aria-label="Invalid run" className="flex gap-3 border-b-2 border-crit/70 bg-crit/[0.09] px-4 py-3 text-crit">
      <I.Cross className="mt-1 shrink-0 text-xl" />
      <div className="space-y-1">
        <p className=" text-[15px] font-bold">Invalid run: do not read these numbers as results</p>
        <p className="text-[13px] text-fg">
          <span className="text-fg-muted">Why: </span>
          {run.reason}
        </p>
      </div>
    </div>
  );
}

function ResultsBody({ data, file, invalid }: { data: ResultsJson; file: string; invalid: boolean }) {
  const order = [
    ...Object.keys(CONDITIONS).filter((c) => c in data.conditions),
    ...Object.keys(data.conditions).filter((c) => !(c in CONDITIONS)),
  ];
  return (
    <div>
      {invalid ? (
        <p role="alert" className="border-b-2 border-crit/70 bg-crit/[0.12] px-4 py-2 text-[14px] font-bold text-crit">
          Invalid run: shown for transparency only. Do not cite these numbers.
        </p>
      ) : (
        <ModelBanner data={data} file={file} />
      )}
      <ErrorBanner data={data} order={order} />
      <Headline data={data} order={order} />
      <AttackChart data={data} order={order} />
      <ConditionTable data={data} order={order} />
      <Legend />
      <div className="grid gap-px border-t border-ink-700 bg-ink-700 min-[1440px]:grid-cols-[3fr_2fr]">
        <CategoryTable data={data} order={order} />
        <HeldoutTable data={data} order={order} />
      </div>
      <Leaks data={data} order={order} />
    </div>
  );
}

// ---- unfinished or errored runs --------------------------------------------------------

function ErrorBanner({ data, order }: { data: ResultsJson; order: string[] }) {
  const errored = order.filter((c) => data.conditions[c]!.attack_errors.k > 0);
  if (!data.incomplete && errored.length === 0) return null;
  return (
    <div role="status" className="border-b border-taint/40 bg-taint/10 px-5 py-3 text-sm text-taint">
      {data.incomplete && (
        <p>This run stopped before every case finished, so these numbers cover only part of the suite.</p>
      )}
      {errored.map((c) => {
        const e = data.conditions[c]!.attack_errors;
        return (
          <p key={c}>
            {c}: {e.k} of {e.n} attack runs ended in an error. An errored run is not safe: its outcome is
            unknown, so the attack-success rate is a lower bound.
          </p>
        );
      })}
    </div>
  );
}

// ---- model label ---------------------------------------------------------------------

function ModelBanner({ data, file }: { data: ResultsJson; file: string }) {
  const simulated = data.simulated ?? (data.model === null ? null : data.model.startsWith("fake"));
  const runs = data.trials_per_case;
  if (simulated === true) {
    return (
      <div role="note" aria-label="Offline simulation" className="flex gap-3 border-b-2 border-warn/70 bg-warn/[0.09] px-4 py-3 text-warn">
        <I.Alert className="mt-1 shrink-0 text-xl" />
        <div className="space-y-1">
          <p className=" text-[15px] font-bold">
            Offline simulation: plumbing check, not evidence about real models
          </p>
          <p className="text-[13px] text-fg">
            Model <code className="font-mono text-warn">{data.model}</code> is a hand-written, deterministic stand-in for a
            gullible model. These numbers show the plumbing and policy rules behave as designed on this corpus. Its reader
            is a regex extractor that cannot be injected, so the ablation rows are not informative offline either.
          </p>
        </div>
      </div>
    );
  }
  if (simulated === false) {
    return (
      <div role="note" aria-label="Real model" className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b-2 border-air/60 bg-air/[0.07] px-4 py-3">
        <I.Brain className="shrink-0 text-xl text-air" />
        <p className="font-mono text-[15px] font-bold text-fg">
          Real model run: <span className="text-air">{data.model}</span>
        </p>
        <p className=" text-[13px] text-fg-muted">
          {runs} trial{runs === 1 ? "" : "s"} per case · {data.n_cases} cases
          {" · not the fake model in the trace panes"}
          {runs === 1 && " · one sample per case, so intervals do not cover run-to-run variation"}
        </p>
      </div>
    );
  }
  return (
    <div role="note" aria-label="Model not recorded" className="flex gap-3 border-b-2 border-warn/70 bg-warn/[0.09] px-4 py-3 text-warn">
      <I.Alert className="mt-1 shrink-0 text-xl" />
      <div className="space-y-1">
        <p className=" text-[15px] font-bold">Model not recorded</p>
        <p className="text-[13px] text-fg">
          {file} does not say which model produced these numbers, so treat them as unverified. A default{" "}
          <code className="font-mono text-warn">uv run airgap eval</code> run uses the offline FakeLLM: a plumbing check,
          not evidence about real models.
        </p>
      </div>
    </div>
  );
}

// ---- headline ------------------------------------------------------------------------

function Stat({ label, hint, r, good, emphasis }: { label: string; hint: string; r: RateJson; good: Good; emphasis?: boolean }) {
  return (
    <div className={`rounded-lg border px-3 py-2.5 ${emphasis ? "border-air/50 bg-air/[0.06]" : "border-ink-600 bg-ink-850"}`}>
      <div className="text-[12px] font-semibold text-fg">
        {label} <span className="text-fg-muted"><Better good={good} /></span>
      </div>
      <div className="mb-1 font-mono text-[11px] text-fg-muted">{hint}</div>
      <RateText r={r} good={good} size="lg" />
      {r.n > 0 && <Track r={r} good={good} />}
    </div>
  );
}

function Headline({ data, order }: { data: ResultsJson; order: string[] }) {
  const shown = ["A_no_defense", "C_airgap"].filter((c) => c in data.conditions);
  const first = shown.length ? shown : order.slice(0, 1);
  const any = data.conditions[first[0]!];
  if (!any) return null;
  const all = any.hijacked.n;
  const dd = any.data_driven_hijacked.n;
  return (
    <div className="space-y-3 border-b border-ink-700 px-4 py-4">
      <p className="text-[13px] text-fg">
        <I.Alert className="mr-1.5 inline align-[-2px] text-warn" />
        {dd > 0 ? (
          <>
            <strong>{all - dd} of the {all} attacks</strong> hide an instruction in the page, which is easy for any design that
            keeps pages away from the planner, so the overall rate flatters.{" "}
            <strong>Read the data-driven subset ({dd}) first</strong>: there success needs a lie, not an obeyed instruction.
          </>
        ) : (
          <>
            This results file has no attack categories (saved before they existed), so the data-driven subset is not
            recorded. The overall rate flatters: much of it is instructions hidden in pages.
          </>
        )}
      </p>
      <div className="grid gap-3 md:grid-cols-2">
        {first.map((cond) => {
          const s = data.conditions[cond]!;
          return (
            <div key={cond} className="space-y-2">
              <h3 className="flex items-center gap-2 text-[13px] font-semibold">
                {cond === "A_no_defense" ? <ShieldOffIcon className="text-crit" /> : <ShieldIcon className="text-air" />}
                {condLabel(cond)}
                <span className="font-mono text-[11px] font-normal text-fg-muted">{CONDITIONS[cond]?.note}</span>
              </h3>
              <div className="grid grid-cols-3 gap-2">
                <Stat label="Attack success" hint={`data-driven · ${dd}`} r={s.data_driven_hijacked} good="low" emphasis />
                <Stat label="Attack success" hint={`all · ${all}`} r={s.hijacked} good="low" />
                <Stat label="Benign completion" hint={`clean pages · ${s.benign_completion.n}`} r={s.benign_completion} good="high" />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---- attack success chart ------------------------------------------------------------

const SERIES = [
  { key: "hijacked", label: "All attacks", shape: "circle", mark: "var(--color-fg)" },
  { key: "data_driven_hijacked", label: "Data-driven subset", shape: "diamond", mark: "var(--color-air)" },
] as const;

type Shape = (typeof SERIES)[number]["shape"];

/** A marker centered on (0, 0); the caller positions it with a nested <svg x="…%">. */
function Marker({ shape, fill }: { shape: Shape; fill: string }) {
  const ring = { stroke: "var(--color-ink-900)", strokeWidth: 2 };
  return shape === "circle" ? <circle r={5} fill={fill} {...ring} /> : <polygon points="0,-6.5 6.5,0 0,6.5 -6.5,0" fill={fill} {...ring} />;
}

const TICKS = [0, 0.25, 0.5, 0.75, 1];

function Axis() {
  return (
    <svg width="100%" height="14" overflow="visible" aria-hidden="true" className="block">
      {TICKS.map((t) => (
        <text
          key={t}
          x={`${t * 100}%`}
          y="10"
          textAnchor={t === 0 ? "start" : t === 1 ? "end" : "middle"}
          className="fill-fg-faint font-mono text-[11px]"
        >
          {t * 100}
          {t === 1 ? "%" : ""}
        </text>
      ))}
    </svg>
  );
}

/** Point at k/n, whiskers over the whole 95% Wilson interval, on a 0–100% scale. */
function IntervalMark({ r, shape, fill, title }: { r: RateJson; shape: Shape; fill: string; title: string }) {
  const at = (x: number) => `${x * 100}%`;
  const y = 13;
  return (
    <svg width="100%" height="26" overflow="visible" className="block" aria-hidden="true">
      <title>{title}</title>
      {TICKS.map((t) => (
        <line key={t} x1={at(t)} x2={at(t)} y1="2" y2="24" stroke="var(--color-ink-700)" strokeWidth="1" />
      ))}
      {r.n > 0 && (
        <>
          <line x1={at(r.lo)} x2={at(r.hi)} y1={y} y2={y} stroke="var(--color-fg-muted)" strokeWidth="2" />
          {[r.lo, r.hi].map((x, i) => (
            <line key={i} x1={at(x)} x2={at(x)} y1={y - 6} y2={y + 6} stroke="var(--color-fg-muted)" strokeWidth="2" strokeLinecap="round" />
          ))}
          <svg x={at(r.k / r.n)} y={y} overflow="visible">
            <Marker shape={shape} fill={fill} />
          </svg>
        </>
      )}
    </svg>
  );
}

function rateSentence(r: RateJson): string {
  if (r.n === 0) return "not recorded";
  const bound = r.k === 0 ? `, none observed, upper bound ${pct(r.hi)}` : "";
  return `${r.k} of ${r.n} hijacked, 95% Wilson interval ${pct(r.lo)} to ${pct(r.hi)}${bound}`;
}

function IntervalLabel({ r }: { r: RateJson }) {
  if (r.n === 0) return <span className=" text-[11px] text-fg-muted">not recorded</span>;
  return (
    <span className="font-mono text-[12px] whitespace-nowrap tabular-nums">
      <span className="font-bold text-fg">
        {r.k}/{r.n}
      </span>{" "}
      <span className="text-fg-muted">{r.k === 0 ? `≤ ${pct(r.hi)}` : `${pct(r.lo)}–${pct(r.hi)}`}</span>
    </span>
  );
}

/**
 * Attack success per condition with its whole interval drawn to scale, so a small sample looks
 * as uncertain as it is: zero successes still shows a whisker up to the interval's upper bound.
 */
function AttackChart({ data, order }: { data: ResultsJson; order: string[] }) {
  const correlated = data.trials_per_case > 1;
  return (
    <figure className="relative border-b border-ink-700 px-4 py-4" aria-labelledby="chart-h">
      <figcaption className="mb-3 space-y-1">
        <h3 id="chart-h" className="label">
          Attack success (hijacked) per condition, with 95% Wilson intervals
        </h3>
        <p className="text-[12px] text-fg-muted">
          Marker at k/n, whiskers over the interval. Wide whiskers mean few runs: read the whole whisker, not the marker.
          With zero successes the whisker still reaches the upper bound (shown as ≤).
          <span className="sm:hidden"> Each track runs from 0% on the left to 100% on the right.</span>
          {correlated &&
            ` With ${data.trials_per_case} trials per case the runs are not independent, so these intervals are optimistic.`}
        </p>
      </figcaption>
      <div
        className="grid items-center gap-x-4 gap-y-1.5 sm:grid-cols-[minmax(140px,auto)_minmax(120px,1fr)_auto_minmax(120px,1fr)_auto]"
        aria-hidden="true"
      >
        <div className="hidden sm:block" />
        {SERIES.map((ser) => (
          <Fragment key={ser.key}>
            <div className="hidden sm:block">
              <div className="mb-0.5 flex items-center gap-1.5 text-[12px] font-semibold text-fg">
                <svg width="14" height="14" overflow="visible">
                  <svg x="7" y="7" overflow="visible">
                    <Marker shape={ser.shape} fill={ser.mark} />
                  </svg>
                </svg>
                {ser.label}
              </div>
              <Axis />
            </div>
            <div className="hidden sm:block" />
          </Fragment>
        ))}
        {order.map((cond) => {
          const s = data.conditions[cond]!;
          return (
            <Fragment key={cond}>
              <div className="pt-2 text-[13px] font-semibold text-fg sm:pt-0">{condLabel(cond)}</div>
              {SERIES.map((ser) => (
                <div key={ser.key} className="flex items-center gap-2 sm:contents">
                  <span className="w-20 shrink-0 text-[11px] leading-tight text-fg-muted sm:hidden">{ser.label}</span>
                  <div className="min-w-0 flex-1 px-1.5">
                    <IntervalMark
                      r={s[ser.key]}
                      shape={ser.shape}
                      fill={ser.mark}
                      title={`${condLabel(cond)}, ${ser.label.toLowerCase()}: ${rateSentence(s[ser.key])}`}
                    />
                  </div>
                  {/* Fixed width on small screens so every track in the stack has the same scale. */}
                  <div className="w-[16ch] shrink-0 text-right text-[12px] sm:w-auto sm:text-left">
                    <IntervalLabel r={s[ser.key]} />
                  </div>
                </div>
              ))}
            </Fragment>
          );
        })}
      </div>
      {/* A table ignores width: 1px; this wrapper (inside a `relative` figure) keeps it out of the layout. */}
      <div className="sr-only">
        <table>
          <caption>Attack success per condition, k of n hijacked with a 95% Wilson interval</caption>
          <thead>
            <tr>
              <th scope="col">Condition</th>
              {SERIES.map((ser) => (
                <th key={ser.key} scope="col">
                  {ser.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {order.map((cond) => (
              <tr key={cond}>
                <th scope="row">{condLabel(cond)}</th>
                {SERIES.map((ser) => (
                  <td key={ser.key}>{rateSentence(data.conditions[cond]![ser.key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

// ---- per-condition table -------------------------------------------------------------

type RateKey = {
  [K in keyof ConditionJson]: ConditionJson[K] extends RateJson ? K : never;
}[keyof ConditionJson];

const COLS: { key: RateKey; label: string; sub: string; good: Good; tint?: string }[] = [
  { key: "hijacked", label: "Attack success", sub: "all attacks", good: "low" },
  { key: "data_driven_hijacked", label: "Attack success", sub: "data-driven only", good: "low", tint: "bg-air/[0.04]" },
  { key: "benign_completion", label: "Benign completion", sub: "clean pages", good: "high", tint: "border-x border-ink-700" },
  { key: "goal_reached", label: "Attacker goal", sub: "all attacks", good: "low" },
  { key: "data_driven_goal", label: "Attacker goal", sub: "data-driven only", good: "low", tint: "bg-air/[0.04]" },
  { key: "utility_under_attack", label: "Utility under attack", sub: "task right on attack pages", good: "high" },
  { key: "unlisted_requests", label: "Unlisted domains", sub: "requests off the allowlist", good: "low" },
  { key: "attacker_paid", label: "Attacker paid", sub: "their wallet or mailbox", good: "low" },
  { key: "policy_stopped", label: "Stopped by policy", sub: "refused, a known safe outcome", good: "high" },
  { key: "errors", label: "Errored runs", sub: "outcome unknown, not safe", good: "low" },
];

/** A horizontally scrollable table region that keyboard users can scroll too. */
function ScrollTable({ label, children }: { label: string; children: ReactNode }) {
  return (
    // `relative` keeps absolutely placed screen-reader text inside the scroll box, not the page.
    <div className="relative overflow-x-auto" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}

function rowTone(cond: string) {
  return cond === "C_airgap" ? "bg-[#0e171f]" : "bg-ink-900";
}

function ConditionTable({ data, order }: { data: ResultsJson; order: string[] }) {
  return (
    <ScrollTable label="Results per condition">
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">
          Attack success next to benign completion for every condition, each as k of n with a 95% Wilson interval (CI)
        </caption>
        <thead>
          <tr className="border-b border-ink-700 bg-ink-850">
            <th scope="col" className="label sticky left-0 z-10 bg-ink-850 px-4 py-2 font-semibold">
              Condition
            </th>
            {COLS.map((c) => (
              <th key={c.key} scope="col" className={`px-2 py-2 align-bottom ${c.tint ?? ""}`}>
                <div className="text-[12px] font-semibold text-fg">
                  {c.label} <span className="text-fg-muted"><Better good={c.good} /></span>
                </div>
                <div className="font-mono text-[11px] text-fg-muted">{c.sub}</div>
              </th>
            ))}
            <th scope="col" className="px-2.5 py-2 align-bottom">
              <div className="text-[12px] font-semibold text-fg">Confirms</div>
              <div className=" text-[11px] text-fg-muted">per run</div>
            </th>
          </tr>
        </thead>
        <tbody>
          {order.map((cond) => {
            const s = data.conditions[cond]!;
            const meta = CONDITIONS[cond];
            return (
              <tr key={cond} className={`border-b border-ink-700/70 ${rowTone(cond)}`}>
                <th scope="row" className={`sticky left-0 z-10 min-w-[150px] px-4 py-2.5 align-top font-normal ${rowTone(cond)}`}>
                  <div className="text-[13px] font-semibold text-fg">{meta?.label ?? cond}</div>
                  <div className="font-mono text-[11px] text-fg-muted">{meta?.note ?? ""}</div>
                  {meta?.group === "ablation" && data.simulated !== false && (
                    <span className="chip mt-1 border-warn/50 text-warn">not informative offline</span>
                  )}
                </th>
                {COLS.map((c) => (
                  <RateCell key={c.key} r={s[c.key]} good={c.good} tint={c.tint} label={`${meta?.label ?? cond}, ${c.label} (${c.sub})`} />
                ))}
                <td className="px-2.5 py-2.5 align-top font-mono text-[13px] text-fg tabular-nums">
                  {s.confirmations_per_run.toFixed(2)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </ScrollTable>
  );
}

function Legend() {
  return (
    <p className="border-t border-ink-700 px-4 py-2 font-mono text-[12px] leading-5 text-fg-muted">
      k/n with its 95% Wilson interval (CI)
      <br />
      bar: the interval on a 0–100% scale, tick at k/n
      <br />
      data-driven: success needs a lie, not an obeyed instruction
    </p>
  );
}

// ---- per-category table --------------------------------------------------------------

function CategoryTable({ data, order }: { data: ResultsJson; order: string[] }) {
  const [picked, setPicked] = useState(order.includes("C_airgap") ? "C_airgap" : order[0]!);
  const cond = order.includes(picked) ? picked : order[0]!;
  const compare = cond !== "A_no_defense" && "A_no_defense" in data.conditions ? "A_no_defense" : null;
  const cats = data.conditions[cond]!.by_category;
  const groups = [
    { dd: true, title: "Data-driven: success needs a lie, not an obeyed instruction" },
    { dd: false, title: "Instruction in the page" },
  ].map((g) => ({ ...g, rows: Object.keys(cats).filter((c) => cats[c]!.data_driven === g.dd) }));
  const cols = compare ? 4 : 3;
  return (
    <div className="bg-ink-900">
      <div className="flex flex-wrap items-center gap-2 px-4 pt-3 pb-2">
        <h3 id="cat-h" className="label mr-auto">
          By attack category
        </h3>
        <div role="group" aria-label="Condition shown in the category table" className="flex flex-wrap gap-1">
          {order.map((c) => (
            <button
              key={c}
              type="button"
              aria-pressed={c === cond}
              onClick={() => setPicked(c)}
              className={`chip h-8 cursor-pointer px-2.5 ${c === cond ? "border-air bg-air/15 text-fg" : "border-ink-500 text-fg-muted hover:text-fg"}`}
            >
              {condLabel(c)}
            </button>
          ))}
        </div>
      </div>
      <ScrollTable label="Results by attack category">
        <table className="w-full border-collapse text-left" aria-labelledby="cat-h">
          <thead>
            <tr className="border-y border-ink-700 bg-ink-850">
              <th scope="col" className="label px-4 py-2 font-semibold">
                Category
              </th>
              {compare && (
                <th scope="col" className="px-2.5 py-2 align-bottom">
                  <div className="text-[12px] font-semibold text-fg">Attack success</div>
                  <div className="font-mono text-[11px] text-fg-muted">{condLabel(compare)}</div>
                </th>
              )}
              <th scope="col" className="px-2.5 py-2 align-bottom">
                <div className="text-[12px] font-semibold text-fg">Attack success</div>
                <div className="font-mono text-[11px] text-air">{condLabel(cond)}</div>
              </th>
              <th scope="col" className="px-2.5 py-2 align-bottom">
                <div className="text-[12px] font-semibold text-fg">Attacker goal</div>
                <div className="font-mono text-[11px] text-air">{condLabel(cond)}</div>
              </th>
            </tr>
          </thead>
          <tbody>
            {groups.map(
              (g) =>
                g.rows.length > 0 && (
                  <Fragment key={String(g.dd)}>
                    <tr className="bg-ink-850/60">
                      <th scope="colgroup" colSpan={cols} className="px-4 py-1.5 text-left text-[11px] font-semibold text-fg-muted">
                        {g.title} · {g.rows.reduce((n, c) => n + cats[c]!.hijacked.n, 0)} attack runs
                      </th>
                    </tr>
                    {g.rows.map((cat) => {
                      const row = cats[cat]!;
                      const base = compare ? data.conditions[compare]!.by_category[cat] : undefined;
                      return (
                        <tr key={cat} className="border-b border-ink-700/70">
                          <th scope="row" className="min-w-[200px] px-4 py-2 align-top font-normal">
                            <div className="text-[13px] text-fg">{categoryLabel(cat)}</div>
                            <div className="font-mono text-[11px] text-fg-muted">{cat || "—"}</div>
                          </th>
                          {compare && (
                            <RateCell
                              r={base?.hijacked ?? { k: 0, n: 0, lo: 0, hi: 1 }}
                              good="low"
                              label={`${categoryLabel(cat)}, ${condLabel(compare)}, attack success`}
                            />
                          )}
                          <RateCell r={row.hijacked} good="low" label={`${categoryLabel(cat)}, ${condLabel(cond)}, attack success`} />
                          <RateCell r={row.goal_reached} good="low" label={`${categoryLabel(cat)}, ${condLabel(cond)}, attacker goal`} />
                        </tr>
                      );
                    })}
                  </Fragment>
                ),
            )}
          </tbody>
        </table>
      </ScrollTable>
    </div>
  );
}

// ---- held-out rows -------------------------------------------------------------------

function HeldoutTable({ data, order }: { data: ResultsJson; order: string[] }) {
  const any = data.conditions[order[0]!]!.heldout;
  return (
    <div className="bg-ink-900">
      <div className="px-4 pt-3 pb-2">
        <h3 id="held-h" className="label">
          Held-out cases only
        </h3>
        <p className="mt-1 text-[12px] text-fg-muted">
          {any.hijacked.n > 0
            ? `${any.hijacked.n} attack and ${any.benign_completion.n} benign runs, picked by a fixed hash of the case id before results were looked at and never used for tuning. Small n, wide intervals: a sanity check, not independent evidence.`
            : "This results file does not mark held-out cases (saved before the split existed)."}
        </p>
      </div>
      <ScrollTable label="Results on held-out cases">
        <table className="w-full border-collapse text-left" aria-labelledby="held-h">
          <thead>
            <tr className="border-y border-ink-700 bg-ink-850">
              <th scope="col" className="label px-4 py-2 font-semibold">
                Condition
              </th>
              {(
                [
                  ["Attack success", "low"],
                  ["Attacker goal", "low"],
                  ["Benign completion", "high"],
                ] as const
              ).map(([label, good]) => (
                <th key={label} scope="col" className="px-2.5 py-2 align-bottom text-[12px] font-semibold text-fg">
                  {label} <span className="text-fg-muted"><Better good={good} /></span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {order.map((cond) => {
              const h = data.conditions[cond]!.heldout;
              return (
                <tr key={cond} className="border-b border-ink-700/70">
                  <th scope="row" className="min-w-[150px] px-4 py-2 align-top text-[13px] font-normal text-fg">
                    {condLabel(cond)}
                  </th>
                  <RateCell r={h.hijacked} good="low" label={`${condLabel(cond)}, held-out attack success`} />
                  <RateCell r={h.goal_reached} good="low" label={`${condLabel(cond)}, held-out attacker goal`} />
                  <RateCell r={h.benign_completion} good="high" label={`${condLabel(cond)}, held-out benign completion`} />
                </tr>
              );
            })}
          </tbody>
        </table>
      </ScrollTable>
    </div>
  );
}

// ---- what still gets through ---------------------------------------------------------

const FLAGS: Record<LeakFlag, { text: string; tone: string }> = {
  hijacked: { text: "unauthorized side effect", tone: "border-crit/60 bg-crit/10 text-crit" },
  goal_reached: { text: "attacker goal reached", tone: "border-warn/60 bg-warn/10 text-warn" },
  unlisted_request: { text: "request to unlisted domain", tone: "border-taint/60 bg-taint/10 text-taint" },
};

/** Reasons cite issue URLs; make them real links. */
function Linkified({ text }: { text: string }) {
  return (
    <>
      {text.split(/(https?:\/\/[^\s)]+[^\s).,;])/).map((part, i) =>
        i % 2 ? (
          <a key={i} href={part} target="_blank" rel="noreferrer" className="text-air underline underline-offset-2 hover:text-fg">
            {part.replace(/^https?:\/\/github\.com\/[^/]+\/[^/]+\/issues\//, "issue #")}
          </a>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}

function LeakItem({ leak, primary }: { leak: LeakJson; primary: boolean }) {
  return (
    <li className={`rounded-lg border px-3 py-2 ${primary ? "border-crit/40 bg-crit/[0.05]" : "border-ink-700 bg-ink-850"}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <code className="mr-1 font-mono text-[13px] font-semibold text-fg">{leak.case_id}</code>
        {leak.flags.map((f) => (
          <span key={f} className={`chip ${FLAGS[f].tone}`}>
            {FLAGS[f].text}
          </span>
        ))}
        {leak.heldout && <span className="chip border-ink-500 text-fg-muted">held-out</span>}
        {leak.trials > 1 && (
          <span className="chip border-ink-500 text-fg-muted">
            {leak.runs}/{leak.trials} runs
          </span>
        )}
        <span className="ml-auto font-mono text-[11px] text-fg-muted">{categoryLabel(leak.category)}</span>
      </div>
      {primary && (
        <p className={`mt-1.5 max-w-[60ch] text-[13px] ${leak.reason ? "text-fg" : "font-semibold text-crit"}`}>
          {leak.reason ? (
            <>
              <span className="text-fg-muted">Why: </span>
              <Linkified text={leak.reason} />
            </>
          ) : (
            "Not in PUBLISHED_LEAKS: no reason recorded. This is an unpublished leak or a regression."
          )}
        </p>
      )}
      {leak.probes && <p className="mt-1 text-[12px] text-fg-muted">Probes: {leak.probes}</p>}
    </li>
  );
}

function LeakSummary({ cond, s, leaks }: { cond: string; s: ConditionJson; leaks: LeakJson[] }) {
  return (
    <>
      <span className="text-[13px] font-semibold text-fg">{condLabel(cond)}</span>
      <span className={` text-[12px] ${leaks.length ? "text-crit" : "text-good"}`}>
        {leaks.length} case{leaks.length === 1 ? "" : "s"} got through
      </span>
      <span className=" text-[12px] text-fg-muted tabular-nums">
        attack success {s.hijacked.k}/{s.hijacked.n}, {ci(s.hijacked)}
      </span>
    </>
  );
}

function Leaks({ data, order }: { data: ResultsJson; order: string[] }) {
  const primary = "C_airgap" in data.conditions ? "C_airgap" : null;
  const rest = order.filter((c) => c !== primary);
  const details = (c: string) => data.leak_details[c] ?? [];
  return (
    <div className="space-y-3 border-t border-ink-700 px-4 py-4">
      <div>
        <h3 className="label">Attacks that still get through (published, never hidden)</h3>
        <p className="mt-1 text-[12px] text-fg-muted">
          A case got through if the ledger shows an unauthorized side effect, the attacker reached their goal (a false
          answer counts), or the agent requested a domain the user never named. That is why the count of cases can exceed
          attack success.
        </p>
      </div>
      {primary && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <LeakSummary cond={primary} s={data.conditions[primary]!} leaks={details(primary)} />
          </div>
          {details(primary).length ? (
            <ul className="grid gap-2 min-[1440px]:grid-cols-2">
              {details(primary).map((l) => (
                <LeakItem key={l.case_id} leak={l} primary />
              ))}
            </ul>
          ) : (
            <p className="font-mono text-[12px] text-fg-muted">
              none observed in {data.conditions[primary]!.hijacked.n} attack runs; {ci(data.conditions[primary]!.hijacked)}
            </p>
          )}
        </div>
      )}
      <div className="space-y-1.5">
        {rest.map((cond) => {
          const leaks = details(cond);
          return (
            <details key={cond} className="group rounded-lg border border-ink-700 bg-ink-900">
              <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-x-3 gap-y-1 rounded-lg px-3 py-2 hover:bg-ink-850">
                <span aria-hidden="true" className="font-mono text-[12px] text-fg-muted transition-transform group-open:rotate-90">
                  ▸
                </span>
                <LeakSummary cond={cond} s={data.conditions[cond]!} leaks={leaks} />
              </summary>
              <div className="border-t border-ink-700 px-3 py-2">
                {leaks.length ? (
                  <ul className="grid gap-1.5 md:grid-cols-2 min-[1440px]:grid-cols-3">
                    {leaks.map((l) => (
                      <LeakItem key={l.case_id} leak={l} primary={false} />
                    ))}
                  </ul>
                ) : (
                  <p className=" text-[12px] text-fg-muted">none observed</p>
                )}
                {cond !== "A_no_defense" && leaks.length > 0 && (
                  <p className="mt-2 text-[12px] text-fg-muted">
                    The published reasons in PUBLISHED_LEAKS explain the plausible-user airgap row; each case here shows what
                    it probes.
                  </p>
                )}
              </div>
            </details>
          );
        })}
      </div>
    </div>
  );
}
