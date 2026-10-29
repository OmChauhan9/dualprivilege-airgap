// Shapes from docs/api-contract.md. Audit payloads are the emit(...) kwargs in
// src/airgap/core/interpreter.py and core/policy.py, passed through unchanged.

export type PaneId = "unguarded" | "airgap";
export type Provider = "fake" | "meta";

export interface Envelope {
  seq: number;
  pane: PaneId | null;
  kind: string;
  data: Record<string, unknown>;
}

export interface Scenario {
  id: string;
  title: string;
  user_text: string;
  channels: string[];
}

export interface StartData {
  scenario: string;
  title: string;
  user_text: string;
  channel: string | null;
  provider: Provider;
  simulated: boolean;
  models: { unguarded_agent: string; orchestrator: string; reader: string };
}

export interface Judgement {
  hijacked: boolean;
  goal_reached: boolean;
  benign_ok: boolean;
}

export interface DoneData {
  reply: string;
  judgement: Judgement;
  payments: { payee: string; amount_usd: number; memo?: string; unauthorized?: boolean }[];
  emails: { to: string; subject?: string; unauthorized?: boolean }[];
  blocked?: string[];
  error?: string | null;
  confirmations: number;
  prompt_tokens: number;
  completion_tokens: number;
  latency_s: number;
}

export interface AgentActionData {
  action: "fetch" | "pay" | "send_email" | "finish" | string;
  url?: string;
  payee?: string;
  amount_usd?: number;
  memo?: string;
  to?: string;
  subject?: string;
  body?: string;
  text?: string;
  reason?: string;
}

export interface PlanData {
  steps: Record<string, unknown>[];
}

export interface FetchData {
  requested_url: string;
  final_url: string;
  domain: string;
  redirects: string[];
  handle: string;
}

export interface SanitizeData {
  handle: string;
  removed: { channel: string; snippet: string }[];
  chars_in: number;
  chars_out: number;
}

export interface ReaderData {
  schema: string;
  value: Record<string, unknown>;
  sources: string[];
}

export interface RuleOfTwo {
  A_untrusted_input: boolean;
  B_sensitive: boolean;
  C_state_change: boolean;
}

export interface PolicyData {
  action: string;
  args: Record<string, unknown>;
  verdict: "allow" | "deny" | "confirm";
  rules: string[];
  reason: string;
  approved: boolean;
  rule_of_two: RuleOfTwo;
}

export interface SteeringData {
  chosen: string;
  compared: { alias: string; value: unknown }[];
  steered_by: string[];
}

export interface AnswerData {
  text: string;
  untrusted_derived: boolean;
}

export interface RateJson {
  k: number;
  n: number;
  lo: number;
  hi: number;
}

export interface ConditionJson {
  hijacked: RateJson;
  goal_reached: RateJson;
  benign_completion: RateJson;
  utility_under_attack: RateJson;
  mean_latency_s: number;
  mean_tokens: number;
  confirmations_per_run: number;
}

export interface ResultsJson {
  model: string | null;
  n_cases?: number;
  trials_per_case?: number;
  conditions: Record<string, ConditionJson>;
  leaks: Record<string, string[]>;
}

export interface ReplayFile {
  simulated: boolean;
  model: string;
  note: string;
  runs: Record<string, Envelope[]>;
}
