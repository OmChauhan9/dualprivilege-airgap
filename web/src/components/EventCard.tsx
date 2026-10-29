import type { ReactNode } from "react";
import { channelLabel, RULES, usd } from "../labels";
import type {
  AgentActionData,
  AnswerData,
  Envelope,
  FetchData,
  PlanData,
  PolicyData,
  ReaderData,
  RuleOfTwo,
  SanitizeData,
  SteeringData,
} from "../types";
import * as I from "./icons";

type Actor = "model" | "code";

function Card({
  icon,
  title,
  actor,
  actorNote,
  seq,
  tone = "default",
  children,
}: {
  icon: ReactNode;
  title: string;
  actor: Actor;
  actorNote: string;
  seq: number;
  tone?: "default" | "crit" | "good" | "warn";
  children: ReactNode;
}) {
  const ring = {
    default: "border-ink-700",
    crit: "border-crit/60 bg-crit/[0.06]",
    good: "border-good/40",
    warn: "border-warn/50",
  }[tone];
  return (
    <li className="animate-enter relative pl-8">
      <span
        aria-hidden="true"
        className={`absolute top-3 left-[9px] grid size-[14px] place-items-center rounded-full border-2 bg-ink-900 ${
          tone === "crit" ? "border-crit" : actor === "model" ? "border-taint" : "border-air"
        }`}
      />
      <article className={`rounded-lg border bg-ink-850 ${ring}`}>
        <header className="flex items-center gap-2 border-b border-ink-700/70 px-3 py-2">
          <span className="text-base text-fg-muted">{icon}</span>
          <h4 className="text-[13px] font-semibold tracking-tight">{title}</h4>
          <span
            className={`chip ${
              actor === "model"
                ? "border-taint/40 text-taint"
                : "border-air/40 text-air"
            }`}
            title={actor === "model" ? "A language model produced this" : "Plain code, no model"}
          >
            {actor === "model" ? "MODEL" : "CODE"} · {actorNote}
          </span>
          <span className="ml-auto font-mono text-[11px] text-fg-faint tabular-nums">#{seq}</span>
        </header>
        <div className="space-y-2 px-3 py-2.5 text-[13px] leading-relaxed">{children}</div>
      </article>
    </li>
  );
}

export function TaintChip({ source, steered }: { source: string; steered?: boolean }) {
  return (
    <span
      className="chip border-taint/50 bg-taint/10 text-taint"
      title={steered ? "Chosen by untrusted data (P6)" : "Depends on untrusted content"}
    >
      <I.Alert className="text-[10px]" />
      {steered ? "steered by " : ""}
      {source}
    </span>
  );
}

const Mono = ({ children, className = "" }: { children: ReactNode; className?: string }) => (
  <code className={`font-mono text-[12px] break-all text-fg ${className}`}>{children}</code>
);

const KV = ({ k, children }: { k: string; children: ReactNode }) => (
  <div className="flex gap-3">
    <dt className="label w-20 shrink-0 pt-0.5">{k}</dt>
    <dd className="min-w-0 flex-1">{children}</dd>
  </div>
);

// ---- unguarded agent ------------------------------------------------------------------

function AgentAction({ ev }: { ev: Envelope }) {
  const d = ev.data as unknown as AgentActionData;
  if (d.action === "fetch") {
    return (
      <Card icon={<I.Globe />} title="Agent fetched a page" actor="model" actorNote="agent" seq={ev.seq}>
        <Mono>{d.url}</Mono>
        <p className="text-fg-muted">
          Raw HTML, hidden text included, is appended to the same context that holds the payment
          and email tools. No provenance check, no sanitizer.
        </p>
      </Card>
    );
  }
  if (d.action === "pay" || d.action === "send_email") {
    const followed = d.reason === "followed page notice";
    return (
      <Card
        icon={<I.Bolt className="text-crit" />}
        title={d.action === "pay" ? "Agent called pay()" : "Agent called send_email()"}
        actor="model"
        actorNote="agent"
        seq={ev.seq}
        tone={followed ? "crit" : "default"}
      >
        <dl className="space-y-1">
          {d.action === "pay" ? (
            <>
              <KV k="payee">
                <Mono className={followed ? "text-crit" : ""}>{d.payee}</Mono>
              </KV>
              <KV k="amount">
                <Mono>{usd(d.amount_usd)}</Mono>
              </KV>
              {d.memo && (
                <KV k="memo">
                  <Mono>{d.memo}</Mono>
                </KV>
              )}
            </>
          ) : (
            <>
              <KV k="to">
                <Mono className={followed ? "text-crit" : ""}>{d.to}</Mono>
              </KV>
              <KV k="subject">
                <Mono>{d.subject}</Mono>
              </KV>
            </>
          )}
        </dl>
        <p className={followed ? "font-medium text-crit" : "text-fg-muted"}>
          {followed
            ? "Instruction came from the page, not the user. The tool ran immediately: nothing sits between the model and the side effect."
            : "The tool ran immediately, with no policy check."}
        </p>
      </Card>
    );
  }
  if (d.action === "finish") {
    return (
      <Card icon={<I.Message />} title="Agent replied" actor="model" actorNote="agent" seq={ev.seq}>
        <p className="text-fg">“{d.text}”</p>
      </Card>
    );
  }
  return (
    <Card icon={<I.Bolt />} title={`Agent: ${d.action}`} actor="model" actorNote="agent" seq={ev.seq}>
      <Mono>{JSON.stringify(d)}</Mono>
    </Card>
  );
}

// ---- airgap pipeline ------------------------------------------------------------------

function fmtArg(a: unknown): string {
  if (a && typeof a === "object") {
    const o = a as Record<string, unknown>;
    if ("lit" in o) return JSON.stringify(o.lit);
    if ("alias" in o) return `@${String(o.alias)}`;
    if ("var" in o) return o.field ? `${String(o.var)}.${String(o.field)}` : String(o.var);
  }
  return JSON.stringify(a);
}

function fmtStep(s: Record<string, unknown>): { op: string; text: string } {
  const op = String(s.op);
  switch (op) {
    case "fetch":
      return { op, text: `${String(s.out)} ← ${fmtArg(s.url)}` };
    case "read":
      return { op, text: `${String(s.out)} ← reader(${String(s.doc)}) as ${String(s.schema_name)}` };
    case "choose_min": {
      const c = (s.candidates as { alias: string; value: unknown }[]) ?? [];
      return { op, text: `${String(s.out)} ← min(${c.map((x) => `${x.alias}: ${fmtArg(x.value)}`).join(", ")})` };
    }
    case "pay":
      return { op, text: `payee=${fmtArg(s.payee)} amount=${fmtArg(s.amount)} memo=${JSON.stringify(s.memo)}` };
    case "send_email":
      return { op, text: `to=${fmtArg(s.to)} subject=${JSON.stringify(s.subject)}` };
    case "answer": {
      const args = Object.entries((s.args as Record<string, unknown>) ?? {})
        .map(([k, v]) => `${k}=${fmtArg(v)}`)
        .join(" ");
      return { op, text: `${JSON.stringify(s.template)} ${args}` };
    }
    default:
      return { op, text: JSON.stringify(s) };
  }
}

function PlanCard({ ev }: { ev: Envelope }) {
  const d = ev.data as unknown as PlanData;
  return (
    <Card icon={<I.Route />} title="Plan (JSON DSL)" actor="model" actorNote="orchestrator" seq={ev.seq}>
      <ol className="space-y-1 rounded-md border border-ink-700 bg-ink-950/60 p-2 font-mono text-[12px]">
        {d.steps.map((s, i) => {
          const { op, text } = fmtStep(s);
          return (
            <li key={i} className="flex gap-2">
              <span className="w-4 shrink-0 text-right text-fg-faint tabular-nums">{i + 1}</span>
              <span className="w-[74px] shrink-0 font-semibold text-air">{op}</span>
              <span className="min-w-0 break-all text-fg">{text}</span>
            </li>
          );
        })}
      </ol>
      <p className="text-fg-muted">
        Written from the user's request alone. The orchestrator never sees page content, so
        nothing on the page can talk to it.
      </p>
    </Card>
  );
}

export function ProvenanceBadge({ domain }: { domain: string }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-md border border-air/50 bg-air/10 px-2 py-1 font-mono text-[12px] text-air"
      title="Registrable domain computed by the fetcher from the final URL. No model can set it."
    >
      <I.Lock />
      <span className="text-fg-muted">origin</span>
      <span className="font-semibold text-fg">{domain}</span>
      <span className="text-[10px] tracking-wider text-air/80 uppercase">fetcher-verified</span>
    </span>
  );
}

function FetchCard({ ev }: { ev: Envelope }) {
  const d = ev.data as unknown as FetchData;
  const hops = d.redirects.length - 1;
  return (
    <Card icon={<I.Globe />} title="Fetch + provenance" actor="code" actorNote="fetcher" seq={ev.seq}>
      <ProvenanceBadge domain={d.domain} />
      <dl className="space-y-1">
        <KV k="url">
          <Mono>{d.final_url}</Mono>
        </KV>
        <KV k="redirects">
          {hops > 0 ? (
            <Mono>{d.redirects.join(" → ")}</Mono>
          ) : (
            <span className="text-fg-muted">none</span>
          )}
        </KV>
        <KV k="handle">
          <Mono>{d.handle}</Mono>
          <span className="ml-2 text-fg-faint">opaque to the orchestrator</span>
        </KV>
      </dl>
    </Card>
  );
}

const PAYLOAD_MARK = /SYSTEM NOTICE/i;

function SanitizeCard({ ev, runChannel }: { ev: Envelope; runChannel: string | null }) {
  const d = ev.data as unknown as SanitizeData;
  const groups = new Map<string, string[]>();
  for (const r of d.removed) groups.set(r.channel, [...(groups.get(r.channel) ?? []), r.snippet]);
  const cut = d.chars_in ? 1 - d.chars_out / d.chars_in : 0;
  const caughtPayload = d.removed.some((r) => PAYLOAD_MARK.test(r.snippet));
  return (
    <Card icon={<I.Scissors />} title="Sanitizer diff" actor="code" actorNote="sanitizer" seq={ev.seq}>
      <div className="flex items-center gap-3 font-mono text-[12px]">
        <span className="text-fg-muted tabular-nums">{d.chars_in.toLocaleString()} chars</span>
        <span className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-ink-700" aria-hidden="true">
          <span
            className="absolute inset-y-0 left-0 rounded-full bg-air"
            style={{ width: `${Math.max(2, (d.chars_out / Math.max(1, d.chars_in)) * 100)}%` }}
          />
        </span>
        <span className="text-fg tabular-nums">{d.chars_out.toLocaleString()} kept</span>
        <span className="text-fg-faint tabular-nums">−{Math.round(cut * 100)}%</span>
      </div>
      {groups.size === 0 ? (
        <p className="text-fg-muted">Nothing hidden to strip.</p>
      ) : (
        <div className="space-y-1.5">
          {[...groups].map(([channel, snippets]) => (
            <div key={channel} className="overflow-hidden rounded-md border border-ink-700">
              <div className="flex items-center gap-2 bg-ink-800 px-2 py-1">
                <span className="chip border-ink-500 text-fg-muted">{channel}</span>
                <span className="text-[11px] text-fg-faint">
                  {snippets.length} removed
                </span>
                {snippets.some((s) => PAYLOAD_MARK.test(s)) && (
                  <span className="chip ml-auto border-crit/60 bg-crit/10 text-crit">
                    <I.Alert /> injected instruction
                  </span>
                )}
              </div>
              {snippets.map((s, i) => (
                <div
                  key={i}
                  className={`flex gap-2 px-2 py-1 font-mono text-[12px] ${
                    PAYLOAD_MARK.test(s) ? "bg-crit/10 text-[#ffb3b3]" : "text-fg-muted"
                  }`}
                >
                  <span aria-hidden="true" className="select-none text-crit">
                    −
                  </span>
                  <span className="min-w-0 break-words">{s || "(empty)"}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
      {runChannel && !caughtPayload && (
        <p className="rounded-md border border-warn/40 bg-warn/[0.07] px-2 py-1.5 text-[12px] text-warn">
          <I.Alert className="mr-1 inline" />
          The {channelLabel(runChannel)} payload is not a hidden channel, so it reaches the reader.
          The sanitizer is attack-surface reduction, not the boundary; the tool-less reader and the
          policy engine are.
        </p>
      )}
    </Card>
  );
}

function ReaderCard({ ev }: { ev: Envelope }) {
  const d = ev.data as unknown as ReaderData;
  return (
    <Card icon={<I.Brain />} title={`Typed read · ${d.schema}`} actor="model" actorNote="reader, quarantined" seq={ev.seq}>
      <table className="w-full border-collapse font-mono text-[12px]">
        <tbody>
          {Object.entries(d.value).map(([k, v]) => (
            <tr key={k} className="border-b border-ink-700/60 last:border-0">
              <td className="py-1 pr-3 text-fg-muted">{k}</td>
              <td className="py-1 pr-3 font-semibold text-fg">{JSON.stringify(v)}</td>
              <td className="py-1 text-right">
                {d.sources.map((s) => (
                  <TaintChip key={s} source={s} />
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-fg-muted">
        No tools, no network, schema-checked JSON. Every field stays tainted wherever it flows.
      </p>
    </Card>
  );
}

export function RuleOfTwoMeter({ r2 }: { r2: RuleOfTwo }) {
  const cells: [string, string, boolean][] = [
    ["A", "untrusted input", r2.A_untrusted_input],
    ["B", "sensitive tools", r2.B_sensitive],
    ["C", "state change", r2.C_state_change],
  ];
  const lit = cells.filter((c) => c[2]).length;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="label">Rule of Two</span>
      <div className="flex gap-1" role="list" aria-label={`Rule of Two: ${lit} of 3 present`}>
        {cells.map(([k, label, on]) => (
          <span
            role="listitem"
            key={k}
            className={`chip ${
              on ? "border-fg bg-fg font-semibold text-ink-950" : "border-ink-600 border-dashed text-fg-faint"
            }`}
            title={`${k}: ${label} ${on ? "present" : "absent"}`}
          >
            <b>{k}</b> {label}
            {on ? <I.Check /> : <span aria-hidden="true">·</span>}
          </span>
        ))}
      </div>
      <span className={`font-mono text-[11px] ${lit === 3 ? "text-warn" : "text-fg-faint"}`}>
        {lit}/3{lit === 3 ? " · all three: gated by policy" : ""}
      </span>
    </div>
  );
}

function verdictBadge(d: PolicyData) {
  if (d.verdict === "allow")
    return { text: "ALLOW", icon: <I.Check />, cls: "border-fg/40 bg-fg/5 text-fg" };
  if (d.verdict === "deny")
    return { text: "DENY · blocked", icon: <I.Shield />, cls: "border-good/60 bg-good/10 text-good" };
  return d.approved
    ? { text: "CONFIRM · user approved", icon: <I.Alert />, cls: "border-warn/60 bg-warn/10 text-warn" }
    : { text: "CONFIRM · user declined", icon: <I.Cross />, cls: "border-warn/60 bg-warn/10 text-warn" };
}

function PolicyCard({ ev }: { ev: Envelope }) {
  const d = ev.data as unknown as PolicyData;
  const b = verdictBadge(d);
  const sideEffect = d.action === "pay" || d.action === "send_email";
  return (
    <Card
      icon={<I.Gavel />}
      title={`Policy · ${d.action}`}
      actor="code"
      actorNote="policy engine"
      seq={ev.seq}
      tone={sideEffect ? (d.verdict === "deny" ? "good" : d.verdict === "confirm" ? "warn" : "default") : "default"}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-mono text-[12px] font-bold ${b.cls}`}>
          {b.icon} {b.text}
        </span>
        {d.rules.map((r) => (
          <span key={r} className="chip border-fg/40 font-bold text-fg" title={RULES[r]}>
            {r}
          </span>
        ))}
        {d.reason && <span className="text-fg-muted">{d.reason}</span>}
      </div>
      <dl className="space-y-0.5">
        {Object.entries(d.args).map(([k, v]) => (
          <KV key={k} k={k}>
            <Mono>{typeof v === "string" ? v : JSON.stringify(v)}</Mono>
          </KV>
        ))}
      </dl>
      {d.rules.length > 0 && (
        <ul className="space-y-0.5 text-[12px] text-fg-muted">
          {d.rules.map((r) => (
            <li key={r}>
              <b className="font-mono text-fg">{r}</b> {RULES[r] ?? ""}
            </li>
          ))}
        </ul>
      )}
      <RuleOfTwoMeter r2={d.rule_of_two} />
    </Card>
  );
}

function SteeringCard({ ev }: { ev: Envelope }) {
  const d = ev.data as unknown as SteeringData;
  return (
    <Card icon={<I.Route />} title="Choice steered by untrusted data" actor="code" actorNote="interpreter" seq={ev.seq} tone="warn">
      <div className="flex flex-wrap gap-2">
        {d.compared.map((c) => (
          <span
            key={c.alias}
            className={`rounded-md border px-2 py-1 font-mono text-[12px] ${
              c.alias === d.chosen ? "border-warn/70 bg-warn/10 text-fg" : "border-ink-600 text-fg-muted"
            }`}
          >
            {c.alias} = {usd(c.value)} {c.alias === d.chosen && <b className="text-warn">← chosen</b>}
          </span>
        ))}
      </div>
      <div className="flex flex-wrap gap-1">
        {d.steered_by.map((s) => (
          <TaintChip key={s} source={s} steered />
        ))}
      </div>
      <p className="text-fg-muted">
        The payee comes from the allowlist, but which one was decided by page data. P6 turns the
        payment into a confirmation.
      </p>
    </Card>
  );
}

function AnswerCard({ ev }: { ev: Envelope }) {
  const d = ev.data as unknown as AnswerData;
  return (
    <Card icon={<I.Message />} title="Reply to user" actor="code" actorNote="interpreter template" seq={ev.seq}>
      <p className="text-fg">“{d.text}”</p>
      {d.untrusted_derived && (
        <p className="flex items-center gap-2 text-[12px] text-fg-muted">
          <TaintChip source="contains untrusted values" /> a fact from the page, not an instruction
        </p>
      )}
    </Card>
  );
}

function ErrorCard({ ev }: { ev: Envelope }) {
  return (
    <Card icon={<I.Alert className="text-crit" />} title="Error (fails closed)" actor="code" actorNote="pipeline" seq={ev.seq} tone="crit">
      <Mono>{String(ev.data.error)}</Mono>
    </Card>
  );
}

export function EventCard({ ev, runChannel }: { ev: Envelope; runChannel: string | null }) {
  switch (ev.kind) {
    case "agent_action":
      return <AgentAction ev={ev} />;
    case "plan":
      return <PlanCard ev={ev} />;
    case "fetch":
      return <FetchCard ev={ev} />;
    case "sanitize":
      return <SanitizeCard ev={ev} runChannel={runChannel} />;
    case "reader_output":
      return <ReaderCard ev={ev} />;
    case "policy":
      return <PolicyCard ev={ev} />;
    case "tainted_control_flow":
      return <SteeringCard ev={ev} />;
    case "answer":
      return <AnswerCard ev={ev} />;
    case "error":
      return <ErrorCard ev={ev} />;
    default:
      return (
        <Card icon={<I.Bolt />} title={ev.kind} actor="code" actorNote="event" seq={ev.seq}>
          <Mono>{JSON.stringify(ev.data)}</Mono>
        </Card>
      );
  }
}

