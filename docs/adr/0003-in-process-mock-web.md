# 3. The mock web runs in-process

Status: accepted

## Context
Attacks are reproduced only against a local mock world with fictional `.test` domains (reserved by RFC 6761). We never touch real malicious sites.

## Decision
`MockWeb` serves host and path routes through `httpx.MockTransport`. The fetcher still uses real HTTP semantics (redirects, headers, status), so provenance logic is exercised for real.

## Consequences
- Tests and CI are hermetic and fast, with no ports or sockets.
- TLS provenance fields are not modeled; they are future work against a real HTTPS fetch.
