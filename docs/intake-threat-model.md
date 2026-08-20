# Submission intake threat model

Status: design gate; intake remains disabled. Last reviewed: 2026-08-20.

## Assets and trust boundaries

The public Worker receives identity proofs and metadata, but never submission
source. Source is fetched later by the existing isolated Actions pipeline at an
exact Git commit. The Worker holds one environment-specific State credential;
it must not hold archive, results-store, release, replay, or repository-fetch
credentials. Production and staging have different Workers, GitHub credentials,
OAuth applications, State repositories, and test identities.

Durable facts are immutable Git events. Cache API and future Cloudflare
rate-limit bindings are transient abuse controls only. A successful HTTP reply
must not be the sole evidence that a submission exists.

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

Metadata amendments require the GitHub identity that owns the base record.
Maintainer repairs and retractions use a separate privileged path and become
publicly attributable State events.

## Principal threats and controls

| Threat | Required control and test |
| --- | --- |
| OAuth login CSRF or callback substitution | One-time state bound to the initiating session, exact callback allowlist, expiry, and reuse tests. |
| Stolen browser token | Minimal scopes, immediate verification, never log or persist the token, redact upstream bodies. |
| Agent impersonation | Commit/tag/gist challenge proof, owner equality, signed expiry, nonce-consumption race test. |
| Ref movement or repository swap | Resolve and record an exact 40-character commit; dispatch uses that commit and allowlisted GitHub hosts only. |
| SSRF through source metadata | Worker never fetches source; downstream fetcher accepts only canonical GitHub repository or gist forms already validated by the secure pipeline. |
| Duplicate or ambiguous requests | Stable idempotency key, immutable ID-derived event path, byte-equivalent replay success, non-forced compare-and-swap update. |
| State corruption | Exact event schemas, causal/state-transition materialization, append-only PR and push monitoring, protected branch, off-platform backup. |
| Credential escalation | Separate least-privilege staging/production credentials scoped to one State repository; no workflow/repository administration permission. |
| Readiness denial of service | Secret-authenticated readiness, short cache, bounded GitHub requests, monitoring. |
| Hostile metadata in site/logs | Length and Unicode policy at intake, schema validation, structured redacted logs, context-appropriate escaping in leaderboard output. |
| Private-source disclosure | Worker never handles source; encrypted archival precedes evaluation; no plaintext artifacts; release requires the separate embargo/key gate. |

## Launch gates

Before `INTAKE_ENABLED` changes to `true`, reviewers must have evidence for:

1. OAuth and agent-challenge contract tests, including nonce contention and
   provider failure;
2. end-to-end synthetic staging intake through archive, evaluation, results v2,
   and State materialization;
3. abuse controls and metadata size/encoding limits;
4. the combined per-submission replay/release key design and recovery drill;
5. State backup and restore, Worker rollback, credential rotation, and CAS
   contention drills; and
6. an incident owner and alert path recorded in `INFRASTRUCTURE.md`.

Issue intake remains available throughout the shadow period. Enabling the
Worker does not authorize closing the issue form; the four-week and adoption
gates are independent.
