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

## Local `/api/v1` contract

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
the raw nonce and signed token never enter State. Production State must deploy
the matching `authentication.nonce_consumed`,
`submission.metadata_amended`, and `submission.publication_changed` schemas
and materializer before this Worker can be enabled.

## GitHub App broker boundary

The approved implementation puts separate source-reader and workflow-dispatch
GitHub App private keys in a private `lean-eval-github-broker-{environment}`
Worker. The public intake Worker reaches it only through the `GITHUB_BROKER`
service binding and sends a strict v1 request containing an audience,
authority, repository, operation, and immutable workflow/source commit where
applicable. The broker mints repository-scoped, one-hour-or-shorter
installation tokens, rejects every non-allowlisted GitHub path, and never
returns a token to intake. Its HTTP protocol is deliberately provider-neutral
at the intake boundary: a later provider can implement the same two authority
operations without changing submission IDs, State, or API routes.

Static `GITHUB_VERIFICATION_TOKEN` and `GITHUB_DISPATCH_TOKEN` hooks remain only
for local contract tests; they are not an approved production credential
design. The two Apps and their broker secrets are not yet provisioned, so the
safe default remains `503` with `INTAKE_ENABLED=false`. Do not request broad
OAuth `repo` scope as a shortcut; browser OAuth intentionally requests only
`read:user`.

GitHub App installation tokens cannot read a submitter's private gist. The
current headless-agent gist proof therefore remains launch-disabled rather
than silently requesting user-token or broad gist authority. Browser OAuth
intake and repository/tag verification do not depend on that proof. Before
agent intake is enabled, replace it with an explicitly reviewed proof that the
source-reader App can verify, or separately approve a GitHub App user-token
flow.

The local workflow emits a digest-verified archive locator artifact, but no
Actions credential is authorized to append the corresponding archive event to
State. Dispatch persistence is local and credential-independent: the intake
CAS adds a strict per-submission view and outbox, request retries reuse it, and
a one-minute scheduled handler reconciles bounded UUIDv7-tail shards. Owner
routes target that view plus its referenced immutable events and never scan the
complete ledger. Provider success removes the outbox; provider failure records
a bounded backoff. State validation must deploy the matching view/outbox
contract before intake is enabled. Correlating the locator's `archive_path` to
its UUID before the archive lifecycle append remains a launch gate. The safe
current behavior is `INTAKE_ENABLED=false`; do not treat a queued State record
or locator artifact alone as a completed pipeline.

Operational view v1 deliberately covers intake, owner mutations, and dispatch;
its archive/evaluation/result fields remain `pending`/`null`. Before lifecycle
writers are enabled, State and Worker must review and deploy a shared v2 view
that materializes those events without weakening targeted-read validation.
