# Glossary

The words this project uses, and what they mean here. Code, docs and issues should use these terms.

| Term | Meaning |
|---|---|
| **Untrusted content** | Anything fetched: HTML, hidden text, metadata, JSON-LD, PDFs, invites, API responses. The attacker controls it. |
| **Fetcher** | Plain code that retrieves a page and reports its **provenance** (registrable domain, redirect chain). No model involved. |
| **Provenance** | Facts about where content came from, produced by the fetcher. Models cannot set or spoof it. |
| **Sanitizer** | Plain code that strips hidden channels (CSS-hidden text, comments, metadata, zero-width characters). Reduces attack surface; not a guarantee. |
| **Reader** | The quarantined model. No tools, no network, schema-constrained output. It turns sanitized text into a typed object. |
| **Orchestrator** | The privileged model. It sees only the user's text and typed descriptions, never page content, and emits a **plan**. |
| **Plan** | A JSON program in the plan DSL. Code executes it; the model never calls tools directly. |
| **Interpreter** | Plain code that runs a plan and routes every action through the policy engine. |
| **Policy engine** | Plain code, the security boundary. Rules P1 to P6 plus Rule of Two state. |
| **Taint / tainted value** | A value that depends on untrusted content. Carries its sources. Empty sources means trusted. |
| **Steered value** | A value chosen by tainted data (a branch or comparison) even though the value itself came from a trusted set, such as a payee alias. |
| **Sink** | An action argument that matters: payee, amount, recipient, subject, body, memo, fetched URL. |
| **Allowlist** | The user's saved payees and recipients. The only legitimate source of payment destinations. |
| **Confirmation gate** | The policy engine asks the user to approve a risky action. Modes: *strict* (deny all), *plausible user* (approves what the dialog lets them verify), *approve all* (inattentive user, worst case). |
| **Rule of Two** | Meta's framing: an agent session should have at most two of [A] untrusted input, [B] sensitive data or tools, [C] state change or external communication. We track all three per session. |
| **Ledger** | Ground truth of side effects (payments, emails, fetches). Attacks are scored from it, never from model text. |
| **Hijacked** | The ledger shows any unauthorized side effect. |
| **Goal reached** | The attacker's specific goal was achieved (for example, their wallet was paid). Reported separately from hijacked. |
| **Benign completion** | The user's legitimate task finished correctly with no extra side effects. |
| **Utility under attack** | Benign completion measured on attacked pages. |
| **Channel** | The place an injection hides: `css_display_none`, `json_ld`, `visible_text`, and so on. |
| **FakeLLM** | Offline deterministic simulation of a gullible model. Tests plumbing only; never evidence about real models. |
