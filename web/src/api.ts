import type { Envelope, Provider, ReplayFile, ResultsJson, Scenario } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function detail(resp: Response): Promise<string> {
  try {
    const body = (await resp.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    return JSON.stringify(body.detail ?? body);
  } catch {
    return resp.statusText || `HTTP ${resp.status}`;
  }
}

export async function fetchScenarios(): Promise<Scenario[]> {
  const resp = await fetch("/api/scenarios");
  if (!resp.ok) throw new ApiError(resp.status, await detail(resp));
  return (await resp.json()) as Scenario[];
}

export type ResultsState =
  | { status: "loading" }
  | { status: "ok"; data: ResultsJson }
  | { status: "missing"; message: string }
  | { status: "error"; message: string };

export async function fetchResults(): Promise<ResultsState> {
  try {
    const resp = await fetch("/api/results");
    if (resp.status === 404) return { status: "missing", message: await detail(resp) };
    if (!resp.ok) return { status: "error", message: await detail(resp) };
    return { status: "ok", data: (await resp.json()) as ResultsJson };
  } catch (err) {
    return { status: "error", message: String(err) };
  }
}

/** Reads the SSE stream with fetch so HTTP errors (e.g. the 403 real-call guard) are visible. */
export async function streamRun(
  params: { scenario: string; channel: string; provider: Provider },
  onMessage: (msg: Envelope) => void,
  signal: AbortSignal,
): Promise<void> {
  const qs = new URLSearchParams(params).toString();
  const resp = await fetch(`/api/run/stream?${qs}`, {
    signal,
    headers: { Accept: "text/event-stream" },
  });
  if (!resp.ok || !resp.body) throw new ApiError(resp.status, await detail(resp));
  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let cut: number;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      const data = frame
        .split("\n")
        .filter((ln) => ln.startsWith("data: "))
        .map((ln) => ln.slice(6))
        .join("\n");
      if (data) onMessage(JSON.parse(data) as Envelope);
    }
  }
}

let replayCache: Promise<ReplayFile> | null = null;

export function loadReplays(): Promise<ReplayFile> {
  replayCache ??= fetch("/replays/fake.json").then(async (resp) => {
    if (!resp.ok) throw new ApiError(resp.status, "replay recordings not found");
    return (await resp.json()) as ReplayFile;
  });
  return replayCache;
}

/** Scenario list rebuilt from recordings, so replay mode works with no API server at all. */
export function scenariosFromReplays(file: ReplayFile): Scenario[] {
  const out = new Map<string, Scenario>();
  for (const [key, msgs] of Object.entries(file.runs)) {
    const [id, channel] = key.split(":") as [string, string];
    const start = msgs[0]?.data as { title?: string; user_text?: string } | undefined;
    const sc = out.get(id) ?? {
      id,
      title: start?.title ?? id,
      user_text: start?.user_text ?? "",
      channels: [],
    };
    if (channel !== "none") sc.channels.push(channel);
    out.set(id, sc);
  }
  return [...out.values()];
}

export interface TraceExport {
  format: "airgap-trace/v1";
  messages: Envelope[];
}

export function parseTrace(text: string): Envelope[] {
  const raw = JSON.parse(text) as unknown;
  const msgs = Array.isArray(raw) ? raw : (raw as Partial<TraceExport>).messages;
  if (!Array.isArray(msgs) || !msgs.every((m) => typeof m === "object" && m && "kind" in m)) {
    throw new Error("not an airgap trace: expected {messages: [...]} or an array of events");
  }
  return msgs as Envelope[];
}
