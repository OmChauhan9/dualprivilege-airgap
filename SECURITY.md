# Security policy

## Reporting a vulnerability

Please report security problems **privately** through GitHub's private vulnerability reporting:
the "Report a vulnerability" button under the repository's **Security** tab. Please do not open a
public issue for something exploitable.

This is a hackathon project maintained by one person, so there is no service-level agreement.
Reports are read and answered as time allows.

## Scope

In scope: the router in `src/airgap/` (fetcher, sanitizer, reader, plan validation, interpreter,
policy engine), the public `Airgap` API and the guard's default real-web transport
(`src/airgap/core/transport.py`), the demo server in `src/airgap/server/`, and the console in
`web/`. Examples that matter most: a path by which untrusted page text reaches the orchestrator or
a payment, email or URL sink without passing the policy engine; a way to make the default transport
connect to a non-public address the developer did not list; a way to make a real model call skip
the spend check; a path traversal or injection in the demo server or console. The check compares an estimate with
the cap before each call. A provider that reports more usage than the estimate can push the total
past the cap.

Out of scope: attacks that the project already lists under "What we do NOT stop" in the README
and in `THREAT_MODEL.md`; compromised user devices; model-weight backdoors; denial of service
against the local demo.

## Ground rules for testing

Use the local mock world (fictional `.test` hosts) where you can. To test the default transport,
use servers you run yourself. Do not point attacks at other people's sites, and do not run real
model calls against anyone's credentials but your own.

## Supported versions

Only the `main` branch is maintained.
