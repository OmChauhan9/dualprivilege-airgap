// Human labels for ids the backend emits. Vocabulary follows GLOSSARY.md.

export const CHANNELS: Record<string, string> = {
  none: "Benign page (no injection)",
  css_display_none: "CSS display:none",
  white_on_white: "White text on white",
  off_screen: "Positioned off-screen",
  tiny_font: "1px font",
  html_comment: "HTML comment",
  zero_width_visible: "Zero-width spaced text",
  json_ld: "JSON-LD metadata",
  og_meta: "OpenGraph meta tag",
  alt_text: "Image alt text",
  visible_text: "Visible text",
};

export const channelLabel = (c: string | null) => CHANNELS[c ?? "none"] ?? c ?? "none";

// From the docstring of src/airgap/core/policy.py.
export const RULES: Record<string, string> = {
  P1: "Payee must be on the allowlist or written by the user",
  P2: "Amount within per-transaction and session limits",
  P3: "Email recipients must be on the allowlist",
  P4: "Tainted values may not reach payee, recipient, subject, body or memo; a tainted amount needs confirmation",
  P5: "Fetch only domains the user named, including after redirects",
  P6: "A choice steered by tainted data needs confirmation",
  R2: "Rule of Two session state",
};

export const CONDITIONS: Record<string, { label: string; note: string; group: "base" | "airgap" | "ablation" }> = {
  A_no_defense: { label: "A · Unguarded agent", note: "single context, raw page + tools", group: "base" },
  B_prompt_defense: { label: "B · Prompt defense", note: "‘ignore instructions in pages’", group: "base" },
  C_airgap: { label: "C · Airgap", note: "plausible-user confirmation", group: "airgap" },
  C_airgap_strict_confirm: { label: "C · Airgap, strict", note: "every confirmation denied", group: "airgap" },
  C_airgap_approve_all: { label: "C · Airgap, approve-all", note: "inattentive user, worst case", group: "airgap" },
  C_no_sanitizer: { label: "Ablation · no sanitizer", note: "reader sees raw markup", group: "ablation" },
  C_no_policy: { label: "Ablation · no policy engine", note: "logs, allows everything", group: "ablation" },
};

export const ROLE_LABELS: Record<string, { label: string; note: string }> = {
  unguarded_agent: { label: "Unguarded agent", note: "one context: prompt, raw page, tools" },
  orchestrator: { label: "Orchestrator", note: "privileged · sees user text only · emits a plan" },
  reader: { label: "Reader", note: "quarantined · no tools · typed output" },
};

export function shortAddr(s: string): string {
  return /^0x[0-9a-f]{20,}$/i.test(s) ? `${s.slice(0, 10)}…${s.slice(-4)}` : s;
}

export function pct(x: number): string {
  const v = x * 100;
  return `${v < 10 && v > 0 ? v.toFixed(1) : v.toFixed(0)}%`;
}

export function usd(x: unknown): string {
  const n = Number(x);
  return Number.isFinite(n)
    ? n.toLocaleString("en-US", { style: "currency", currency: "USD" })
    : String(x);
}
