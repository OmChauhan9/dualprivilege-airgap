import { useEffect, useRef } from "react";
import { shortAddr, usd } from "../labels";
import type { DoneData, PaneId, PolicyData, StartData } from "../types";
import type { PaneView } from "../usePlayer";
import { EventCard } from "./EventCard";
import * as I from "./icons";

const META: Record<PaneId, { title: string; sub: string; flow: string[][]; accent: string; icon: typeof I.Shield }> = {
  unguarded: {
    title: "Unguarded",
    sub: "Vulnerable baseline agent",
    flow: [
      ["user text", "agent model", "pay · email tools"],
      ["page", "raw HTML, hidden text and all", "same context"],
    ],
    accent: "from-crit-mark/80",
    icon: I.ShieldOff,
  },
  airgap: {
    title: "Airgapped",
    sub: "Dual-privilege router · policy engine is the boundary",
    flow: [
      ["user text", "orchestrator", "plan", "policy", "tools"],
      ["page", "fetcher", "sanitizer", "reader", "typed value"],
    ],
    accent: "from-air/80",
    icon: I.Shield,
  },
};

function Flow({ steps }: { steps: string[] }) {
  return (
    <div className="flex flex-wrap items-center gap-1 font-mono text-[11px] text-fg-muted">
      {steps.map((s, i) => (
        <span key={s} className="flex items-center gap-1">
          {i > 0 && <span className="text-fg-faint" aria-hidden="true">→</span>}
          <span className="rounded border border-ink-600 bg-ink-850 px-1.5 py-px">{s}</span>
        </span>
      ))}
    </div>
  );
}

export type PaneStatus = "idle" | "running" | "done";

function StatusPill({ status }: { status: PaneStatus }) {
  const map = {
    idle: ["IDLE", "bg-ink-500"],
    running: ["RUNNING", "bg-air animate-pulse"],
    done: ["DONE", "bg-fg-muted"],
  } as const;
  const [text, dot] = map[status];
  return (
    <span className="chip border-ink-600 text-fg-muted">
      <span className={`size-1.5 rounded-full ${dot}`} aria-hidden="true" />
      {text}
    </span>
  );
}

function LedgerAlert({ done }: { done: DoneData }) {
  const bad = [
    ...done.payments
      .filter((p) => p.unauthorized ?? done.judgement.hijacked)
      .map((p) => ({ key: `p-${p.payee}-${p.amount_usd}`, kind: "PAYMENT", text: `${usd(p.amount_usd)} → ${shortAddr(p.payee)}`, full: p.payee })),
    ...done.emails
      .filter((e) => e.unauthorized ?? done.judgement.hijacked)
      .map((e) => ({ key: `e-${e.to}`, kind: "EMAIL", text: `→ ${e.to}`, full: e.to })),
  ];
  if (!done.judgement.hijacked || bad.length === 0) return null;
  return (
    <div role="alert" className="animate-alarm mx-4 mb-3 rounded-lg border-2 border-crit bg-crit/15 px-3 py-2.5">
      <div className="flex items-center gap-2 text-[12px] font-bold text-crit">
        <I.Alert className="text-base" /> Unauthorized side effect on the ledger
      </div>
      <ul className="mt-1.5 space-y-1">
        {bad.map((b) => (
          <li key={b.key} className="flex items-baseline gap-2 font-mono text-[13px]">
            <span className="rounded bg-crit px-1.5 py-px text-[11px] font-bold text-ink-950">{b.kind}</span>
            <span className="font-semibold text-fg" title={b.full}>
              {b.text}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-1.5 text-[12px] text-fg-muted">Scored from the ledger (ground truth), never from model text.</p>
    </div>
  );
}

function Verdict({ id, done, start, approvedConfirm }: { id: PaneId; done: DoneData; start: StartData | null; approvedConfirm: boolean }) {
  const attack = start?.channel != null;
  const j = done.judgement;
  const head = j.hijacked
    ? { icon: <I.ShieldOff />, text: "HIJACKED", sub: j.goal_reached ? "attacker’s goal reached" : "unauthorized side effect", cls: "border-crit bg-crit/10 text-crit" }
    : attack
      ? { icon: <I.Shield />, text: "ATTACK CONTAINED", sub: "no unauthorized side effects on the ledger", cls: "border-good/70 bg-good/10 text-good" }
      : { icon: <I.Check />, text: "CLEAN RUN", sub: "benign page, no injection", cls: "border-fg/40 bg-fg/5 text-fg" };
  return (
    <section aria-live="polite" aria-label={`${id} verdict`} className="space-y-2 border-t border-ink-700 bg-ink-850/80 px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className={`inline-flex items-center gap-2 rounded-lg border-2 px-3 py-1.5 font-mono text-[15px] font-bold ${head.cls}`}>
          {head.icon} {head.text}
        </span>
        <span className="text-[13px] text-fg-muted">{head.sub}</span>
      </div>
      <p className="flex items-center gap-2 text-[13px]">
        {j.benign_ok ? <I.Check className="text-good" /> : <I.Cross className="text-crit" />}
        <span>
          User’s task {j.benign_ok ? "completed correctly" : <b>not completed correctly</b>}
        </span>
        {done.reply && <span className="truncate text-fg-muted" title={done.reply}>· “{done.reply}”</span>}
      </p>
      {done.blocked && done.blocked.length > 0 && (
        <ul className="space-y-0.5 text-[12px] text-fg-muted">
          {done.blocked.map((b) => (
            <li key={b}>
              <span className="text-good">blocked</span> {b}
            </li>
          ))}
        </ul>
      )}
      {id === "airgap" && j.hijacked && approvedConfirm && (
        <p className="rounded-md border border-warn/40 bg-warn/[0.07] px-2 py-1.5 text-[12px] text-warn">
          <I.Alert className="mr-1 inline" />
          Not stopped: this attack stays inside policy. The confirmation dialog showed a saved payee
          and a plausible amount, so a careful user approved it. A page lying about its own data
          is out of reach for this design (ADR 0001).
        </p>
      )}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-fg-faint tabular-nums">
        <span>confirmations {done.confirmations}</span>
        <span>tokens {done.prompt_tokens + done.completion_tokens}</span>
        <span>latency {done.latency_s.toFixed(3)}s</span>
        {done.error && <span className="text-crit">error: {done.error}</span>}
      </div>
    </section>
  );
}

export function Pane({ id, view, start, status }: { id: PaneId; view: PaneView; start: StartData | null; status: PaneStatus }) {
  const m = META[id];
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [view.events.length]);
  const approvedConfirm = view.events.some(
    (e) => e.kind === "policy" && (e.data as unknown as PolicyData).verdict === "confirm" && (e.data as unknown as PolicyData).approved,
  );
  const Icon = m.icon;
  return (
    <section aria-label={`${m.title} agent`} className="panel relative flex min-w-0 flex-col overflow-hidden">
      <div className={`absolute inset-x-0 top-0 h-0.5 bg-gradient-to-r ${m.accent} to-transparent`} aria-hidden="true" />
      <header className="space-y-2 px-4 pt-4 pb-3">
        <div className="flex items-center gap-2.5">
          <Icon className={`text-xl ${id === "unguarded" ? "text-crit" : "text-air"}`} />
          <h2 className=" text-[15px] font-bold">{m.title}</h2>
          <span className="text-[13px] text-fg-muted">{m.sub}</span>
          <span className="ml-auto">
            <StatusPill status={status} />
          </span>
        </div>
        {m.flow.map((f) => (
          <Flow key={f.join()} steps={f} />
        ))}
      </header>
      {view.done && <LedgerAlert done={view.done} />}
      <div ref={scroller} className="grid-bg relative h-[560px] overflow-y-auto border-t border-ink-700 px-3 py-3">
        {view.events.length === 0 ? (
          <div className="grid h-full place-items-center text-center text-[13px] text-fg-faint">
            {status === "running" ? "waiting for the first event…" : "Pick a scenario and press Run."}
          </div>
        ) : (
          <ol className="relative space-y-2.5 before:absolute before:top-2 before:bottom-2 before:left-[15px] before:w-px before:bg-ink-600" aria-label={`${m.title} trace`}>
            {view.events.map((ev) => (
              <EventCard key={ev.seq} ev={ev} runChannel={start?.channel ?? null} />
            ))}
          </ol>
        )}
      </div>
      {view.done ? (
        <Verdict id={id} done={view.done} start={start} approvedConfirm={approvedConfirm} />
      ) : (
        <div className="border-t border-ink-700 bg-ink-850/80 px-4 py-3 text-[12px] text-fg-faint">
          verdict pending
        </div>
      )}
    </section>
  );
}
