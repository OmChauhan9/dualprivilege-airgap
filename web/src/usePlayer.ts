import { useCallback, useEffect, useRef, useState } from "react";
import type { DoneData, Envelope, PaneId, StartData } from "./types";

// Playback paces events at a fixed cadence so a human can follow them, and plays both panes in
// parallel. Given the same input, it reveals the same frames in the same order: that is what
// makes replay mode reproducible for a demo video.

export interface PaneView {
  events: Envelope[];
  done: DoneData | null;
}

export interface RunView {
  start: StartData | null;
  panes: Record<PaneId, PaneView>;
  ended: boolean;
  error: string | null;
  received: Envelope[];
}

export type Speed = 0.5 | 1 | 2 | 4 | "instant";

const TICK_MS = 180;
// Ticks to dwell after revealing each kind, so heavier cards get reading time.
const DWELL: Record<string, number> = {
  plan: 4,
  fetch: 3,
  sanitize: 5,
  reader_output: 4,
  policy: 4,
  tainted_control_flow: 5,
  agent_action: 4,
  answer: 3,
  error: 3,
  done: 1,
};
const PANES: PaneId[] = ["unguarded", "airgap"];

const empty = (): RunView => ({
  start: null,
  panes: { unguarded: { events: [], done: null }, airgap: { events: [], done: null } },
  ended: false,
  error: null,
  received: [],
});

export function usePlayer(speed: Speed) {
  const [view, setView] = useState<RunView>(empty);
  const queues = useRef<Record<PaneId, Envelope[]>>({ unguarded: [], airgap: [] });
  const dwell = useRef<Record<PaneId, number>>({ unguarded: 0, airgap: 0 });
  const inputEnded = useRef(false);
  const timer = useRef<number | null>(null);
  const speedRef = useRef(speed);

  const reveal = useCallback((msg: Envelope) => {
    setView((v) => {
      if (msg.pane === null) return v;
      const pane = v.panes[msg.pane];
      const next =
        msg.kind === "done"
          ? { ...pane, done: msg.data as unknown as DoneData }
          : { ...pane, events: [...pane.events, msg] };
      return { ...v, panes: { ...v.panes, [msg.pane]: next } };
    });
  }, []);

  const stop = useCallback(() => {
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = null;
  }, []);

  const tick = useCallback(() => {
    for (const p of PANES) {
      if (dwell.current[p] > 0) {
        dwell.current[p] -= 1;
        continue;
      }
      const msg = queues.current[p].shift();
      if (msg) {
        reveal(msg);
        dwell.current[p] = (DWELL[msg.kind] ?? 3) - 1;
      }
    }
    const drained = PANES.every((p) => queues.current[p].length === 0 && dwell.current[p] === 0);
    if (inputEnded.current && drained) {
      stop();
      setView((v) => ({ ...v, ended: true }));
    }
  }, [reveal, stop]);

  const ensureTimer = useCallback(() => {
    if (speedRef.current === "instant") {
      for (const p of PANES) {
        for (const m of queues.current[p].splice(0)) reveal(m);
        dwell.current[p] = 0;
      }
      if (inputEnded.current) setView((v) => ({ ...v, ended: true }));
      return;
    }
    if (timer.current === null) {
      timer.current = window.setInterval(tick, TICK_MS / speedRef.current);
    }
  }, [reveal, tick]);

  useEffect(() => {
    speedRef.current = speed;
    if (timer.current !== null) {
      stop();
      ensureTimer();
    }
  }, [speed, stop, ensureTimer]);

  useEffect(() => stop, [stop]);

  const reset = useCallback(() => {
    stop();
    queues.current = { unguarded: [], airgap: [] };
    dwell.current = { unguarded: 0, airgap: 0 };
    inputEnded.current = false;
    setView(empty());
  }, [stop]);

  const push = useCallback(
    (msg: Envelope) => {
      setView((v) => ({ ...v, received: [...v.received, msg] }));
      if (msg.pane === null) {
        if (msg.kind === "start") {
          setView((v) => ({ ...v, start: msg.data as unknown as StartData }));
        } else if (msg.kind === "error") {
          setView((v) => ({ ...v, error: String(msg.data.error ?? "error") }));
        } else if (msg.kind === "end") {
          inputEnded.current = true;
        }
      } else {
        queues.current[msg.pane].push(msg);
      }
      ensureTimer();
    },
    [ensureTimer],
  );

  /** The input broke off (network error, abort): show what we have and stop. */
  const fail = useCallback(
    (message: string) => {
      inputEnded.current = true;
      setView((v) => ({ ...v, error: message }));
      ensureTimer();
    },
    [ensureTimer],
  );

  return { view, push, reset, fail };
}
