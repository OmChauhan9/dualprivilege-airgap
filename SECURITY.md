# Security policy

## Reporting a vulnerability

Please report security problems **privately** through GitHub's private vulnerability reporting:
the "Report a vulnerability" button under the repository's **Security** tab. Please do not open a
public issue for something exploitable.

This is a hackathon project maintained by one person, so there is no service-level agreement.
Reports are read and answered as time allows.

## Scope

In scope: the router in `src/airgap/` (fetcher, sanitizer, reader, plan validation, interpreter,
policy engine), the demo server in `src/airgap/server/`, and the console in `web/`. Examples that
matter most: a path by which untrusted page text reaches the orchestrator or a payment, email or
URL sink without passing the policy engine; a way to make a real model call spend money without
the spend cap; a path traversal or injection in the demo server or console.

Out of scope: attacks that the project already lists under "What we do NOT stop" in the README
and in `THREAT_MODEL.md`; compromised user devices; model-weight backdoors; denial of service
against the local demo.

## Ground rules for testing

Use only the local mock world (fictional `.test` hosts). Do not point attacks at real sites, and
do not run real model calls against anyone's credentials but your own.

## Supported versions

Only the `main` branch is maintained.
