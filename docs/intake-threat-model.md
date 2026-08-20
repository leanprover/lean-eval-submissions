# Submission intake threat model

Status: design gate; intake remains disabled. Last reviewed: 2026-08-20.

## Assets and trust boundaries

The public Worker receives identity proofs and metadata, but never submission
source. Source is fetched later by the existing isolated Actions pipeline at an
exact Git commit. The Worker holds one environment-specific State credential;
it must not hold archive, results-store, release, replay, or repository-fetch
credentials. Production and staging have different Workers, GitHub credentials,
OAuth applications, State repositories, and test identities.

Durable facts are immutable Git events. Strict targeted submission views and
dispatch outboxes are derived operational indexes committed by the same State
CAS and validated against their referenced events; Git history preserves their
transitions. Cache API and the Cloudflare rate-limit binding are transient abuse
controls only. A successful HTTP reply must not be the sole evidence that a
submission exists.

## Authentication contracts

Browser intake uses GitHub OAuth with a one-time, expiring, session-bound state
value. The callback verifies the token against GitHub, obtains only the stable
authenticated login/identity required by policy, and discards the OAuth token
before any State append or workflow dispatch.

Agent intake uses a server-created, signed, expiring challenge bound to:

- the asserted GitHub identity;
- the exact repository and immutable commit;
- a tag pointing at that commit;
- a secret gist owned by the same identity; and
- a single-use nonce.

Only a domain-separated digest of a consumed nonce is durable. Replays,
expired challenges, moved tags, mismatched gist owners, and non-exact refs fail
closed. Submission IDs and event IDs are lowercase UUIDv7 values generated only
after successful authentication. Retried requests retain their originally
allocated identities through an explicit idempotency contract; they do not
mint parallel submissions.

Nonce consumption and a new intake are one Git compare-and-swap batch. The
signed grant preallocates UUIDv7 identities, while the first accepted request
records its actual acceptance time. The targeted view preserves that batch;
retries read it instead of fabricating issuance-time occurrences. A partial or
colliding batch fails closed. OAuth-state consumption records the actual
callback time and a byte-identical existing event is replay, not success.

Metadata amendments require the GitHub identity that owns the base record.
Maintainer repairs and retractions use a separate privileged path and become
publicly attributable State events.

## Principal threats and controls

| Threat | Required control and test |
| --- | --- |
| OAuth login CSRF or callback substitution | One-time state bound to the initiating session, exact callback allowlist, expiry, and reuse tests. |
| Stolen browser token | Minimal scopes, immediate verification, never log or persist the token, redact upstream bodies. |
| Agent impersonation | The current commit/tag/gist challenge remains disabled because installation tokens cannot read private gists; enable only after a reviewed App-verifiable replacement, with owner equality, signed expiry, and nonce-consumption race tests. |
| Ref movement or repository swap | Resolve and record the source's exact 40-character commit. GitHub dispatch accepts only branch/tag refs, so the workflow uses a protected `lean-eval-dispatch/<commit>` tag, carries the embedded commit as an input, and checks it against `GITHUB_SHA` before any source access. |
| SSRF through source metadata | Worker never fetches source; downstream fetcher accepts only canonical GitHub repository or gist forms already validated by the secure pipeline. |
| Duplicate or ambiguous requests | Stable idempotency key, immutable ID-derived event path, byte-equivalent replay success, non-forced compare-and-swap update. |
| State corruption | Exact event schemas, causal/state-transition materialization, append-only validation, and protected branches. |
| Credential escalation | Separate least-privilege staging/production credentials scoped to one State repository; no workflow/repository administration permission. |
| Readiness denial of service | Secret-authenticated readiness, short cache, bounded GitHub requests, monitoring. |
| Intake abuse | Cloudflare Rate Limiting binding keyed by route and a hashed credential or multi-signal anonymous actor key; distinct staging/production namespaces; binding denial or error fails closed with `429`. |
| Dispatch failure after State acceptance | Same-CAS outbox, targeted view, bounded scheduled reconciliation, per-submission workflow concurrency, and deterministic result/event identities. |
| Hostile metadata in site/logs | Length and Unicode policy at intake, schema validation, structured redacted logs, context-appropriate escaping in leaderboard output. |
| Private-source disclosure | Worker never handles source; encrypted archival precedes evaluation; no plaintext artifacts; release requires the separate embargo/key gate. |
| Direct workflow-dispatch bypass | Server-only inputs are exact-field decoded again in Python; workflow-tag commit, source visibility, and source commit are revalidated; only the requested problem/revision is recordable; UUID archive locator is mandatory. |

## Credential provisioning still required

Agent tag verification for private repositories and server workflow dispatch
cannot use the discarded browser OAuth token. The selected implementation is a
private Cloudflare service-binding broker with separate source-reader and
workflow-dispatch Apps. Static verification/dispatch token hooks are
local-contract scaffolding only. The Apps and secrets still require operator
creation; with those credentials absent, requests fail with `503`, and with
`INTAKE_ENABLED=false`, every `/api/` route fails before auth or provider work.
Broadening OAuth to `repo` or `gist` is not an acceptable fallback.

## Launch gates

Before `INTAKE_ENABLED` changes to `true`, reviewers must have evidence for:

1. OAuth and agent-challenge contract tests, including nonce contention and
   provider failure;
2. end-to-end synthetic staging intake through archive, evaluation, results v2,
   and State materialization;
3. abuse controls and metadata size/encoding limits;
4. the combined per-submission replay/release key design;
5. State validation and CAS-contention tests; and
6. an operational owner recorded in `INFRASTRUCTURE.md`.

Issue intake remains available throughout the shadow period. Enabling the
Worker does not authorize closing the issue form; the four-week and adoption
gates are independent.
