import type { ResultsState } from "../api";
import { CONDITIONS, pct } from "../labels";
import type { RateJson } from "../types";
import * as I from "./icons";

type Good = "low" | "high";

/** k/n first, Wilson interval always beside it. The track is a 0-100% scale; never a bare percent. */
function RateCell({ r, good, label }: { r: RateJson; good: Good; label: string }) {
  const bad = good === "low" ? r.k > 0 : r.k < r.n;
  const mark = good === "low" ? (r.k > 0 ? "bg-crit-mark" : "bg-fg-muted") : "bg-good-mark";
  const point = r.n ? r.k / r.n : 0;
  const ci = `95% CI ${pct(r.lo)}–${pct(r.hi)}`;
  return (
    <td className="px-3 py-2.5 align-top">
      <div
        className="min-w-[150px]"
        tabIndex={0}
        aria-label={`${label}: ${r.k} of ${r.n}, ${ci}`}
        title={`${label}: ${r.k} of ${r.n} · Wilson ${ci}`}
      >
        <div className="flex items-baseline gap-2">
          <span className={`font-mono text-[15px] font-bold tabular-nums ${good === "low" && bad ? "text-crit" : "text-fg"}`}>
            {r.k}/{r.n}
          </span>
          <span className="font-mono text-[11px] text-fg-muted tabular-nums">{ci}</span>
        </div>
        <div className="relative mt-1.5 h-2 rounded-full bg-ink-700" aria-hidden="true">
          <span
            className={`absolute inset-y-0 rounded-full ${mark} opacity-70`}
            style={{ left: `${r.lo * 100}%`, width: `max(4px, ${(r.hi - r.lo) * 100}%)` }}
          />
          <span
            className="absolute -top-0.5 -bottom-0.5 w-0.5 rounded bg-fg ring-2 ring-ink-900"
            style={{ left: `calc(${point * 100}% - 1px)` }}
          />
        </div>
      </div>
    </td>
  );
}

const COLS: { key: "hijacked" | "goal_reached" | "benign_completion" | "utility_under_attack"; label: string; good: Good; hint: string }[] = [
  { key: "hijacked", label: "Attack success", good: "low", hint: "any unauthorized side effect · attack pages" },
  { key: "goal_reached", label: "Attacker goal reached", good: "low", hint: "their wallet paid / inbox reached" },
  { key: "benign_completion", label: "Benign completion", good: "high", hint: "user task done right · clean pages" },
  { key: "utility_under_attack", label: "Utility under attack", good: "high", hint: "user task done right · attack pages" },
];

export function ResultsPanel({ state }: { state: ResultsState }) {
  return (
    <section className="panel overflow-hidden" aria-labelledby="results-h">
      <header className="flex flex-wrap items-center gap-3 border-b border-ink-700 px-4 py-3">
        <I.Flask className="text-lg text-air" />
        <h2 id="results-h" className="font-mono text-[14px] font-bold tracking-[0.16em] uppercase">
          Evaluation results
        </h2>
        <span className="font-mono text-[12px] text-fg-faint">GET /api/results · results/eval.json</span>
        {state.status === "ok" && (
          <span className="ml-auto flex flex-wrap items-center gap-2 font-mono text-[12px] text-fg-muted">
            <span className="chip border-ink-600">model {state.data.model ?? "not recorded"}</span>
            {state.data.n_cases != null && <span className="chip border-ink-600">{state.data.n_cases} cases</span>}
            {state.data.trials_per_case != null && (
              <span className="chip border-ink-600">{state.data.trials_per_case} trial/case</span>
            )}
          </span>
        )}
      </header>
      {state.status === "loading" && <p className="px-4 py-6 text-fg-faint">loading…</p>}
      {state.status === "missing" && (
        <div className="space-y-2 px-4 py-6">
          <p className="text-fg">No saved evaluation yet.</p>
          <p className="text-[13px] text-fg-muted">
            Run <code className="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-fg">uv run airgap eval</code>, then
            reload. Results are shown as k/n with Wilson intervals, next to benign completion.
          </p>
        </div>
      )}
      {state.status === "error" && <p className="px-4 py-6 text-crit">Could not load results: {state.message}</p>}
      {state.status === "ok" && <ResultsBody data={state.data} />}
    </section>
  );
}

function ResultsBody({ data }: { data: Extract<ResultsState, { status: "ok" }>["data"] }) {
  const simulated = data.model === null || data.model.startsWith("fake");
  const order = [...Object.keys(CONDITIONS).filter((c) => c in data.conditions), ...Object.keys(data.conditions).filter((c) => !(c in CONDITIONS))];
  const leakRows = order.filter((c) => c !== "A_no_defense");
  return (
    <div>
      {simulated && (
        <div className="flex gap-2 border-b border-warn/30 bg-warn/[0.07] px-4 py-2.5 text-[13px] text-warn">
          <I.Alert className="mt-0.5 shrink-0" />
          <p>
            {data.model === null
              ? "results/eval.json does not record which model produced it. If it is the default offline run, these numbers come from the FakeLLM"
              : "These numbers come from the offline FakeLLM"}
            : a plumbing check, not evidence about real models. Offline, the ablation rows are not
            informative either: the fake reader is a regex extractor that cannot be injected.
          </p>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <caption className="sr-only">Attack success and benign completion per condition, as k of n with 95% Wilson intervals</caption>
          <thead>
            <tr className="border-b border-ink-700 bg-ink-850">
              <th scope="col" className="label px-4 py-2 font-medium">Condition</th>
              {COLS.map((c) => (
                <th key={c.key} scope="col" className="px-3 py-2 align-bottom">
                  <div className="text-[12px] font-semibold text-fg">{c.label}</div>
                  <div className="font-mono text-[10px] text-fg-faint">
                    {c.good === "low" ? "▼ lower is better" : "▲ higher is better"} · {c.hint}
                  </div>
                </th>
              ))}
              <th scope="col" className="px-3 py-2 align-bottom text-[12px] font-semibold text-fg">Confirms / run</th>
            </tr>
          </thead>
          <tbody>
            {order.map((cond) => {
              const s = data.conditions[cond]!;
              const meta = CONDITIONS[cond];
              return (
                <tr key={cond} className={`border-b border-ink-700/70 ${cond === "C_airgap" ? "bg-air/[0.05]" : ""} ${meta?.group === "ablation" ? "opacity-75" : ""}`}>
                  <th scope="row" className="px-4 py-2.5 align-top font-normal">
                    <div className="text-[13px] font-semibold text-fg">{meta?.label ?? cond}</div>
                    <div className="font-mono text-[11px] text-fg-faint">{meta?.note ?? ""}</div>
                  </th>
                  {COLS.map((c) => (
                    <RateCell key={c.key} r={s[c.key]} good={c.good} label={`${meta?.label ?? cond} ${c.label}`} />
                  ))}
                  <td className="px-3 py-2.5 align-top font-mono text-[13px] text-fg tabular-nums">{s.confirmations_per_run.toFixed(2)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="grid gap-3 border-t border-ink-700 px-4 py-3 md:grid-cols-2 xl:grid-cols-3">
        <h3 className="label md:col-span-2 xl:col-span-3">Attacks that still succeed (published, never hidden)</h3>
        {leakRows.map((cond) => {
          const leaks = data.leaks[cond] ?? [];
          const s = data.conditions[cond]!;
          return (
            <div key={cond} className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[13px] font-semibold">{CONDITIONS[cond]?.label ?? cond}</span>
                <span className="font-mono text-[11px] text-fg-faint tabular-nums">
                  {s.hijacked.k}/{s.hijacked.n}
                </span>
              </div>
              {leaks.length ? (
                <ul className="mt-1.5 flex flex-wrap gap-1">
                  {leaks.map((l) => (
                    <li key={l} className="chip border-crit/60 bg-crit/10 text-crit">
                      <I.Alert /> {l}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1.5 font-mono text-[12px] text-fg-muted">
                  none observed in n={s.hijacked.n}; true rate could still be up to {pct(s.hijacked.hi)} (95% upper bound)
                </p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
