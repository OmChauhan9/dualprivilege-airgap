import type { ReactNode } from "react";
import { channelLabel, ROLE_LABELS } from "../labels";
import type { Provider, Scenario, StartData } from "../types";
import type { Speed } from "../usePlayer";
import * as I from "./icons";

export type Mode = "live" | "replay";

export function FakeBadge() {
  return (
    <div
      role="note"
      className="flex items-center gap-2 rounded-lg border-2 border-warn/80 bg-warn/10 px-3 py-1.5 text-warn"
    >
      <I.Flask className="shrink-0 text-base" />
      <div className="leading-tight">
        <div className="font-mono text-[12px] font-bold tracking-wider uppercase">Offline fake model</div>
        <div className="text-[11px] text-warn/90">plumbing check, not evidence about real models</div>
      </div>
    </div>
  );
}

export function Header({ showFake, mode, api }: { showFake: boolean; mode: Mode; api: "up" | "down" | "checking" }) {
  return (
    <header className="flex flex-wrap items-center gap-4">
      <div className="flex items-center gap-3">
        <svg viewBox="0 0 32 32" className="size-9" aria-hidden="true">
          <rect width="32" height="32" rx="7" fill="#0e131a" stroke="#263243" />
          <path d="M10 8v16" stroke="#ff5c5c" strokeWidth="3" strokeLinecap="round" />
          <path d="M22 8v16" stroke="#5cc8ff" strokeWidth="3" strokeLinecap="round" />
          <path d="M14 16h4" stroke="#7d8b9c" strokeWidth="2" strokeDasharray="1 2" />
        </svg>
        <div>
          <h1 className="font-mono text-[17px] font-bold tracking-[0.14em] uppercase">
            DualPrivilege<span className="text-air">·</span>Airgap
          </h1>
          <p className="text-[13px] text-fg-muted">
            Agents read the untrusted web without being handed the keys. Isolation enforced in code, not prompts.
          </p>
        </div>
      </div>
      <div className="ml-auto flex flex-wrap items-center gap-3">
        <span
          className={`chip ${mode === "live" ? "border-air/50 text-air" : "border-taint/50 text-taint"}`}
          title={mode === "live" ? "Streaming from the API" : "Playing stored events at a fixed cadence"}
        >
          {mode === "live" ? "● LIVE" : "▶ REPLAY · deterministic"}
        </span>
        <span className="chip border-ink-600 text-fg-muted" title="API server status">
          <span
            aria-hidden="true"
            className={`size-1.5 rounded-full ${api === "up" ? "bg-good" : api === "down" ? "bg-crit" : "bg-ink-500"}`}
          />
          api {api === "up" ? "online" : api === "down" ? "offline" : "…"}
        </span>
        {showFake && <FakeBadge />}
      </div>
    </header>
  );
}

function Field({ label, htmlFor, children }: { label: string; htmlFor?: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={htmlFor} className="label">
        {label}
      </label>
      {children}
    </div>
  );
}

const selectCls =
  "h-9 rounded-md border border-ink-600 bg-ink-850 px-2.5 text-[13px] text-fg hover:border-ink-500 disabled:opacity-50";

function Segmented<T extends string>({
  value,
  options,
  onChange,
  disabled,
  name,
}: {
  value: T;
  options: { value: T; label: string; title?: string }[];
  onChange: (v: T) => void;
  disabled?: boolean;
  name: string;
}) {
  return (
    <div role="radiogroup" aria-label={name} className="flex h-9 rounded-md border border-ink-600 bg-ink-850 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          disabled={disabled}
          title={o.title}
          onClick={() => onChange(o.value)}
          className={`rounded px-3 font-mono text-[12px] font-semibold transition-colors disabled:opacity-50 ${
            value === o.value ? "bg-ink-600 text-fg" : "text-fg-muted hover:text-fg"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export interface ControlsProps {
  scenarios: Scenario[];
  scenario: string;
  channel: string;
  provider: Provider;
  mode: Mode;
  speed: Speed;
  running: boolean;
  canExport: boolean;
  onScenario: (id: string) => void;
  onChannel: (c: string) => void;
  onProvider: (p: Provider) => void;
  onMode: (m: Mode) => void;
  onSpeed: (s: Speed) => void;
  onRun: () => void;
  onExport: () => void;
  onLoadFile: (f: File) => void;
}

export function Controls(p: ControlsProps) {
  const sc = p.scenarios.find((s) => s.id === p.scenario);
  return (
    <div className="panel flex flex-wrap items-end gap-4 px-4 py-3">
      <Field label="Scenario" htmlFor="scenario">
        <select id="scenario" className={`${selectCls} w-[330px] max-w-full`} value={p.scenario} onChange={(e) => p.onScenario(e.target.value)} disabled={p.running}>
          {p.scenarios.map((s) => (
            <option key={s.id} value={s.id}>
              {s.id} · {s.title}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Injection channel" htmlFor="channel">
        <select id="channel" className={`${selectCls} w-[230px]`} value={p.channel} onChange={(e) => p.onChannel(e.target.value)} disabled={p.running}>
          <option value="none">{channelLabel("none")}</option>
          {(sc?.channels ?? []).map((c) => (
            <option key={c} value={c}>
              {channelLabel(c)}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Source">
        <Segmented
          name="Source"
          value={p.mode}
          onChange={p.onMode}
          disabled={p.running}
          options={[
            { value: "live", label: "Live", title: "Run both agents on the server now" },
            { value: "replay", label: "Replay", title: "Play stored events deterministically" },
          ]}
        />
      </Field>
      <Field label="Provider">
        <Segmented
          name="Provider"
          value={p.provider}
          onChange={p.onProvider}
          disabled={p.running || p.mode === "replay"}
          options={[
            { value: "fake", label: "fake", title: "Offline FakeLLM (default)" },
            { value: "meta", label: "meta", title: "Meta Model API. The server refuses unless AIRGAP_ALLOW_REAL_CALLS=1." },
          ]}
        />
      </Field>
      <Field label="Speed" htmlFor="speed">
        <select
          id="speed"
          className={`${selectCls} w-[92px]`}
          value={String(p.speed)}
          onChange={(e) => p.onSpeed(e.target.value === "instant" ? "instant" : (Number(e.target.value) as Speed))}
        >
          <option value="0.5">0.5×</option>
          <option value="1">1×</option>
          <option value="2">2×</option>
          <option value="4">4×</option>
          <option value="instant">instant</option>
        </select>
      </Field>
      <button
        type="button"
        onClick={p.onRun}
        disabled={p.running || !sc}
        className="flex h-9 items-center gap-2 rounded-md bg-air px-5 font-mono text-[13px] font-bold tracking-wider text-ink-950 uppercase shadow-[0_0_24px_-6px_rgb(92_200_255/0.7)] hover:bg-[#86d8ff] disabled:cursor-not-allowed disabled:opacity-50"
      >
        <I.Play /> {p.running ? "Running…" : "Run"}
      </button>
      <div className="ml-auto flex items-center gap-2">
        <button
          type="button"
          onClick={p.onExport}
          disabled={!p.canExport}
          className="flex h-9 items-center gap-1.5 rounded-md border border-ink-600 px-3 text-[12px] text-fg-muted hover:border-ink-500 hover:text-fg disabled:opacity-40"
          title="Download this run's events for deterministic replay"
        >
          <I.Download /> Export trace
        </button>
        <label
          className="flex h-9 cursor-pointer items-center gap-1.5 rounded-md border border-ink-600 px-3 text-[12px] text-fg-muted focus-within:outline-2 focus-within:outline-air hover:border-ink-500 hover:text-fg"
          title="Replay an exported trace file"
        >
          <I.Upload /> Load trace
          <input
            type="file"
            accept="application/json,.json"
            className="sr-only"
            disabled={p.running}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) p.onLoadFile(f);
              e.target.value = "";
            }}
          />
        </label>
      </div>
    </div>
  );
}

function highlightUrls(text: string) {
  return text.split(/(https?:\/\/\S+?)(?=[.,;:!?]?(?:\s|$))/g).map((part, i) =>
    /^https?:\/\//.test(part) ? (
      <span key={i} className="rounded bg-ink-700 px-1 font-mono text-[13px] text-air">
        {part}
      </span>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

export function Brief({ scenario, channel, source }: { scenario: Scenario | undefined; channel: string; source: string | null }) {
  if (!scenario) return <div className="panel px-4 py-3 text-fg-faint">loading scenarios…</div>;
  return (
    <div className="panel space-y-2.5 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="label">Scenario</span>
        <span className="text-[14px] font-semibold">{scenario.title}</span>
        {source && <span className="chip ml-auto border-ink-600 text-fg-faint">{source}</span>}
      </div>
      <p className="flex gap-3 text-[15px] leading-relaxed">
        <span className="mt-0.5 shrink-0 rounded bg-fg px-1.5 py-px font-mono text-[11px] font-bold text-ink-950">USER</span>
        <span>{highlightUrls(scenario.user_text)}</span>
      </p>
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <span className="label">Page served to both agents</span>
        {channel === "none" ? (
          <span className="chip border-ink-500 text-fg">clean page, no injection</span>
        ) : (
          <span className="chip border-crit/60 bg-crit/10 text-crit">
            <I.Alert /> poisoned · {channelLabel(channel)}
          </span>
        )}
        <span className="text-fg-faint">fictional .test domains, in-process mock web</span>
      </div>
      <div className="grid gap-2 border-t border-ink-700 pt-2.5 text-[13px] md:grid-cols-2">
        <p className="flex gap-2 text-fg-muted">
          <I.ShieldOff className="mt-0.5 shrink-0 text-crit" />
          <span>
            <b className="text-fg">Left:</b> page text lands in the same context as the tools, so an
            instruction hidden in the page can call them directly.
          </span>
        </p>
        <p className="flex gap-2 text-fg-muted">
          <I.Shield className="mt-0.5 shrink-0 text-air" />
          <span>
            <b className="text-fg">Right:</b> only a tool-less reader sees the page. The planner sees
            the user's words, and every side effect passes plain-code policy.
          </span>
        </p>
      </div>
    </div>
  );
}

export function ModelsPanel({ start, provider }: { start: StartData | null; provider: Provider }) {
  const simulated = start ? start.simulated : provider === "fake";
  return (
    <section className="panel px-4 py-3" aria-labelledby="models-h">
      <div className="flex items-center gap-2">
        <h2 id="models-h" className="label">
          Meta models used
        </h2>
        {start && (
          <span className={`chip ml-auto ${simulated ? "border-warn/60 text-warn" : "border-air/60 text-air"}`}>
            provider {start.provider}
          </span>
        )}
      </div>
      {simulated && (
        <p className="mt-1 text-[12px] text-warn">None in this run: the offline fake-llm stands in for every role.</p>
      )}
      <dl className="mt-2 space-y-1.5">
        {Object.entries(ROLE_LABELS).map(([role, meta]) => (
          <div key={role} className="flex items-baseline gap-3">
            <dt className="w-[118px] shrink-0 text-[13px] font-semibold">{meta.label}</dt>
            <dd className="min-w-0">
              <div className="font-mono text-[13px] text-fg">
                {start ? start.models[role as keyof StartData["models"]] : "run to see"}
              </div>
              <div className="text-[11px] text-fg-faint">{meta.note}</div>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
