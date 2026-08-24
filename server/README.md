# lean-eval submission Worker

This directory contains the Cloudflare Worker that will replace GitHub Issues
as the submission intake boundary. It is intentionally deployed with
`INTAKE_ENABLED=false` until the state repositories, GitHub credentials, abuse
controls, and replay key design have passed their rollout gates.

The first implemented primitive is append-only Git state. Each durable event is
stored in its own `events/<id-prefix>/<event-id>.json` file. The ID-derived
partition makes retries independent of wall-clock date. Event IDs are random
UUIDv7 values; an API retry must retain the originally allocated event and
submission IDs. The Worker decoder intentionally accepts only
`system.initialized` and `submission.received`, the root events it is permitted
to append. The State repository owns the broader causal lifecycle registry.
A write reads one
branch head, creates a tree and commit, and advances `main` with a non-forced
reference update. A competing writer causes the whole decision to be retried
from the new head; an uncertain GitHub response is checked for reachability and
never reported as a definite failure when the outcome cannot be established.

## Local checks

```bash
npm ci
npm run check
```

`npm run types` regenerates `worker-configuration.d.ts` from `wrangler.jsonc`;
CI uses `npm run types:check` so a stale checked-in binding file fails instead
of being silently rewritten. The test suite runs inside Cloudflare's local
Workers runtime and includes an integration call through the deployed module
entrypoint; GitHub compare-and-swap branches remain covered with mocked API
responses.
Local secrets belong in the ignored `.dev.vars` file. Never add token values to
Wrangler configuration, tests, documentation, or workflow command lines.

## Deployment

Merges to protected `main` that touch this directory deploy staging, run a
smoke test, then deploy production and run the same smoke test. GitHub
Environments named `cloudflare-staging` and `cloudflare-production` hold their
own deployment credentials. See [`../INFRASTRUCTURE.md`](../INFRASTRUCTURE.md)
for the complete inventory, ownership, setup, and recovery record.
The temporary intake-disabled rollout uses the isolated `lean-eval.workers.dev`
account subdomain. Preview URLs remain disabled. The provider-neutral API,
State, archive, and broker contracts allow a later move to an organization
account or another provider without changing stable identities.
The authentication and source-boundary design is recorded in
[`../docs/intake-threat-model.md`](../docs/intake-threat-model.md); every launch
gate there remains mandatory while intake is disabled.

## Replay executor

`wrangler.replay.jsonc`, `Dockerfile.replay`, and `src/replay-*.ts` define the
separate Cloudflare Sandbox replay boundary. It is automatically deployed after
merge with the broker and intake Worker, but general replay stays disabled in
both environments. Staging exposes the synthetic acceptance route and the
separate accepted-archive boundary route only to the exact protected
`replay-staging` GitHub environment via GitHub OIDC. The accepted-archive route
verifies a State-selected ciphertext, plaintext digest, safe tar shape, blocked
egress, and destruction without running the checker or writing State.
Production acceptance and replay are both disabled.

Each replay creates a fresh nonce-derived Sandbox, uses no persistent default
session, and starts one fixed background image command with public networking
disabled. Short, freshly authenticated status requests poll that exact process;
the terminal poll validates its bounded output and calls `destroy()` before
returning source-free evidence. This avoids making a multi-hour evaluator's
lifetime depend on one HTTP/RPC connection. The image deletes the encoded
identity and ciphertext immediately after decoding and removes every decrypted
or source-derived file in its unconditional cleanup. The five-minute Sandbox
sleep remains a cleanup fallback if the controller disappears. The container
is limited to one `standard-4` instance (12 GiB) with SSH disabled. That 12 GiB
ceiling is the reviewed production profile; exceeding it is a resource-limit
outcome and does not authorize a larger unreviewed executor. Production remains
disabled until the other replay gates pass. `npm run deploy:dry-run` validates
Worker configuration without building a local container; normal protected-main
deployment performs the real image build and rollout.

## Operational readiness

`GET /healthz` is public and secret-free. It distinguishes the reviewed
configured state from the Worker-enforced effective state, reports
`disabled`, `leased`, `durable`, or fail-closed `invalid` mode, and exposes only
the exact lease expiry (never the nonce or controller bindings). Authenticated
`GET /readyz` reports normal dependency readiness only when intake is
effectively enabled. Authenticated
`POST /readyz` is the State-writer preflight used before enablement and after
credential rotation: it reads the current State head and submits a non-forced
update of the branch to that same commit. Success therefore proves repository
read/write authority and the ruleset bypass without changing State. The
preflight uses `READINESS_TOKEN`, remains available while intake is disabled,
and is invoked through `verify-state-writer.yml` so the State credential never
leaves its Worker secret binding.

Production enablement first deploys a finite `leased` configuration bound to
the exact controller commit/run/attempt, target commit, State commit, event,
and nonce digest. Every public API request recomputes effective state and fails
closed at the exact expiry second even if rollout automation disappears. The
authenticated lease smoke consumes the nonce by an exact-head State CAS; its
event is deterministic so a committed response loss is idempotently
recoverable. Only after that proof may protected deployment make a final
`durable` deployment with all lease bindings absent. Tracked configuration may
contain only closed `disabled` or `durable` mode; lease material is generated
ephemerally by the controller and is never committed.

## Local API version 1 (`/api/v1`) contract

The intake-disabled build implements these routes for local workerd and
contract testing:

- `GET /api/v1/oauth/start` and `GET /api/v1/oauth/callback` perform a
  session-bound, ten-minute GitHub OAuth flow. The provider token exists only
  while `/user` is verified and is then discarded. A one-hour, HttpOnly,
  same-origin session is signed by the Worker.
- `POST /api/v1/browser/submission-grants` allocates a signed, expiring,
  single-use UUIDv7 grant; `POST /api/v1/browser/submissions` consumes it.
- `POST /api/v1/agent/challenges` binds an asserted lowercase login, secret
  gist ID, repository, exact commit, prescribed tag, expiry, nonce, and
  preallocated UUIDv7s. `POST /api/v1/agent/submissions` requires the private
  `lean-eval-proof.txt` gist owned by that identity to contain the exact signed
  challenge and the prescribed tag to resolve directly to the submitted
  commit (annotated tags are dereferenced once).
- `GET /api/v1/submissions/<uuid>`,
  `PATCH /api/v1/submissions/<uuid>/metadata`, and
  `PUT /api/v1/submissions/<uuid>/publication` require the base record's
  owning identity. Mutations also require a canonical UUIDv7
  `Idempotency-Key`; cookie-authenticated mutations are same-origin.

All JSON objects use exact-field decoders, request bodies are limited to 16
KiB, and submitter-controlled text has explicit Unicode/control-character and
size rules. Formalization-evaluation and software-verification sources must be
private; open-conjecture sources must be public. GitHub's observed visibility
must equal the declaration. GitHub accepts only a branch or tag name as the
`workflow_dispatch` `ref`, so dispatch uses an immutable tag named
`lean-eval-dispatch/<40-character-commit>` and carries that commit as a
required input. The first server-only workflow step compares the input to
`GITHUB_SHA` before checkout, source access, or evaluation. Repository rules
must reject updates and deletion for these tags. The lane revalidates the
exact source commit and visibility, archives under the canonical submission
UUID path, and requires the verified locator defined by
`../schemas/archive-locator-v1.schema.json` before recording.

Consumed nonces are committed atomically with intake when applicable. Their
`occurred_at` is the actual first-accept/callback time, not the earlier grant
issuance time; retries read the targeted stored view rather than reconstructing
different event timestamps. State
stores only `SHA256("lean-eval-auth-nonce-v1\\0" + purpose + "\\0" + nonce)`;
the raw nonce and signed token never enter State. Production and staging State
deploy the matching `authentication.nonce_consumed`,
`submission.metadata_amended`, and `submission.publication_changed` schemas
and materializer. The lifecycle-aware submission view (wire schema version 2)
additionally
authenticates its referenced archive, evaluation, and result events without
scanning the full ledger. Staging uses this contract for the end-to-end
fixture; production intake remains disabled.

## GitHub App broker boundary

The approved implementation puts separate source-reader and workflow-dispatch
GitHub App private keys in a private `lean-eval-github-broker-{environment}`
Worker. The public intake Worker reaches it only through the `GITHUB_BROKER`
service binding and sends a strict broker-protocol schema version 1 request
containing an audience,
authority, repository, operation, and immutable workflow/source commit where
applicable. The broker mints repository-scoped, one-hour-or-shorter
installation tokens, rejects every non-allowlisted GitHub path, and never
returns a token to intake. Its HTTP protocol is deliberately provider-neutral
at the intake boundary: a later provider can implement the same two authority
operations without changing submission IDs, State, or API routes.

Static `GITHUB_VERIFICATION_TOKEN` and `GITHUB_DISPATCH_TOKEN` hooks remain only
for local contract tests; they are not an approved production credential
design. The two Apps and their broker secrets were provisioned on 2026-08-21;
the Apps are owned by `leanprover`; transfers preserved their IDs and the
dispatcher installation. The safe production default remains
`INTAKE_ENABLED=false`. Do not request
broad OAuth `repo` scope as a shortcut; browser OAuth intentionally requests
only `read:user`.

GitHub secret gists are unlisted rather than private. Headless-agent proof
fetches the exact high-entropy gist ID anonymously, then requires
`public: false`, the asserted GitHub owner, an untruncated
`lean-eval-proof.txt`, and the
verbatim signed expiring challenge. The broker deliberately continues to
reject `/gists/`; no user token or App gist authority is required. Repository
metadata and immutable-tag verification still use the source-reader App.

The local workflow emits a digest-verified archive locator artifact. Separate
source-free callback jobs use the matching environment's lifecycle token to
append `archive.completed` or a classified `archive.failed`, followed where
applicable by `evaluation.started` and the exact accepted, rejected, or failed
evaluation terminal event, while atomically advancing the
targeted submission view. No State or callback credential enters the untrusted
evaluation or archive job. Dispatch persistence is credential-independent: the intake
CAS adds a strict per-submission view and outbox, request retries reuse it, and
a one-minute scheduled handler reconciles bounded UUIDv7-tail shards. Owner
routes target that view plus its referenced immutable events and never scan the
complete ledger. Provider success removes the outbox; provider failure records
a bounded backoff. State validation must deploy the matching view/outbox
contract before intake is enabled. Submission-view schema version 1 remains
readable for pre-lifecycle records; any lifecycle append upgrades the same
canonical path to the strict lifecycle-aware schema.

Automatic protected-main deployment has one staging-only promotion exception
while ordinary intake remains disabled. Authenticated `POST
/internal/v1/promotion-canary` accepts a deterministic, withheld synthetic
fixture for the exact `DEPLOYED_COMMIT` and its matching immutable dispatch
tag plus the deployment `GITHUB_RUN_ID`/`GITHUB_RUN_ATTEMPT`. Polls for that
tuple reuse the exact submission and evidence; a workflow rerun creates fresh
material. From one current branch snapshot the State adapter creates two
sibling commits, applies one, observes the other's real forward-only 409/422
collision, then rebuilds/retries and verifies the evidence on the new head. The
winner is an intentional empty-tree barrier commit: the distinct commit object
advances the ref without changing the State tree, which remains valid under the
repository's full-tree and append-only CI checks.

The request leaves the fixed-`ca`-shard dispatch outbox pending. The actual Cron
Trigger discovers all strict run-scoped canaries, contains source-free errors,
and calls the normal broker reconciliation/State-success path. The broker
targets the dedicated permissionless `promotion-canary.yml` no-op at the exact
tag; it never dispatches the full submission workflow or creates audit,
evaluation, Results, or release records. The synthetic timestamp is derived in
a fixed 2026-08-20 window solely for deterministic IDs. The production config
explicitly disables this authority, and the route rejects every non-staging
runtime. A succeeded dispatch proves GitHub accepted the exact
`workflow_dispatch` through broker/reconciliation; it does not prove the
asynchronous no-op job completed. Responses contain no fixture contents,
credentials, or upstream response bodies.

State independently reconstructs archive/evaluation/result summaries from the
immutable event graph and rejects a stale or fabricated view. The safe current
behavior remains `INTAKE_ENABLED=false` until the staged live path and all
other rollout gates pass; do not treat a queued State record alone as a
completed pipeline.
