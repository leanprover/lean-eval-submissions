# lean-eval infrastructure inventory

This file is the source of truth for externally hosted lean-eval
infrastructure. A change to Cloudflare, GitHub credentials, deployment credentials,
state repositories, runner topology, DNS, or release storage is incomplete
until this ledger changes in the same pull request or an immediately linked
operations pull request. Secret **names, owners, scopes, and rotation dates**
belong here; secret values do not.

Last reconciled: 2026-08-20 (separate temporary account created; Worker
resources below are not yet provisioned). Temporary owner: Kim Morrison.
Target owner: leanprover organization administrators. Service code:
[`server/`](server/).

## Provisioning status

| Resource | Desired identifier | Environment | Status |
| --- | --- | --- | --- |
| Cloudflare account | `lean-eval` (`a46b90978a1c29cc4795f30677e7e4b8`) | temporary shared | **PROVISIONED 2026-08-20** |
| Cloudflare Worker | `lean-eval-submission-server-staging` | staging | **TO BE PROVISIONED** |
| Cloudflare Worker | `lean-eval-submission-server` | production | **TO BE PROVISIONED** |
| Temporary Worker route | `lean-eval-submission-server-staging.lean-eval.workers.dev` | staging | **TO BE PROVISIONED; INTAKE DISABLED** |
| Temporary Worker route | `lean-eval-submission-server.lean-eval.workers.dev` | production | **TO BE PROVISIONED; INTAKE DISABLED** |
| Target Worker custom domain | `eval-submit-staging.lean-lang.org` | staging | **DEFERRED; ZONE ABSENT** |
| Target Worker custom domain | `eval-submit.lean-lang.org` | production | **DEFERRED; ZONE ABSENT** |
| GitHub state repository | `leanprover/lean-eval-state-staging` | staging | **TO BE CREATED** |
| GitHub state repository | `leanprover/lean-eval-state` | production | **TO BE CREATED** |
| GitHub generator repository | `leanprover/lean-eval-generator` | shared | **TO BE CREATED** |
| GitHub release repository | `leanprover/lean-eval-releases` | production | **TO BE CREATED** |
| GitHub Environment | `cloudflare-staging` | staging | **TO BE CREATED** |
| GitHub Environment | `cloudflare-production` | production | **TO BE CREATED** |
| Replay execution backend | Lean-Eval-owned disposable executor | production | **TO BE DESIGNED AND PROVISIONED** |

Do not change a status to provisioned without replacing every applicable
placeholder in the inventory below and recording a verification date.

## Cloudflare account and zone

| Field | Recorded value |
| --- | --- |
| Account name | `lean-eval` |
| Account ID | `a46b90978a1c29cc4795f30677e7e4b8` |
| Workers subdomain | `lean-eval.workers.dev` |
| Excluded account | `Kim@lean-fro.org's Account` (`d789bf36d237e0cb313be59b927c82bd`); contains Palomar Workers and must not host Lean Eval |
| Zone | none; target `lean-lang.org` will not be present in the temporary account |
| Zone ID | not applicable until organization-account migration |
| Billing plan / cost owner | Free / Kim Morrison (temporary) |
| Primary administrator | Kim Morrison (`kim@lean-fro.org`) |
| Backup administrator | none; **REQUIRED BEFORE INTAKE OR PUBLICATION** |

Worker configuration is declarative in [`server/wrangler.jsonc`](server/wrangler.jsonc):

- compatibility date `2026-08-20` with `nodejs_compat`;
- `workers.dev` enabled temporarily in both environments; preview URLs disabled;
- full Workers observability enabled;
- a transient API rate-limit binding at 30 calls per 60 seconds, with distinct
  account-unique namespaces `24012001` (staging) and `24012002` (production);
- a one-minute UTC Cron Trigger in each environment for bounded dispatch
  outbox reconciliation;
- distinct Worker names, exact temporary `workers.dev` routes, variables,
  credentials, and state repositories for staging and production;
- intake disabled by default and enabled only through a reviewed configuration
  change after the rollout gates pass.

Every deployment injects `DEPLOYED_COMMIT` from the Git commit being deployed.
The smoke gate validates that marker, the target environment, and the expected
intake setting, so a healthy stale or misrouted Worker cannot be promoted. The
smoke assertion that intake is false must change in the same reviewed rollout
that enables intake.

There is no Terraform layer. Wrangler configuration plus this reviewed ledger
is the chosen infrastructure record. Resource identifiers created outside
Wrangler must be copied here immediately.

The namespace IDs are user-defined positive integers and must remain unique in
the Cloudflare account; bindings with the same ID share counters. Configuration
and locality semantics follow Cloudflare's
[Rate Limiting binding documentation](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/).
Cron Triggers are managed only through Wrangler as documented by
[Cloudflare](https://developers.cloudflare.com/workers/configuration/cron-triggers/).

The temporary `workers.dev` endpoints are public and are not the production
hostname design. They exist only for intake-disabled deployment and rollback
drills. The temporary account has no `lean-lang.org` zone,
so these drills do not test
custom-domain DNS or routing. Before intake, migrate to an
organization-controlled account, add a separate backup administrator, disable
`workers.dev`, restore the two target custom domains, rotate credentials, and
repeat every deployment, OAuth, and rollback drill.

## Deployment automation

[`deploy-worker.yml`](.github/workflows/deploy-worker.yml) is the only normal
deployment path. A change under `server/` merged to protected `main` runs:

1. locked dependency install, generated binding types, typecheck, lint, tests,
   dependency audit, and Wrangler dry run;
2. staging deploy and `GET /healthz` smoke test;
3. production deploy and `GET /healthz` smoke test.

GitHub environment `cloudflare-staging` must contain:

| Name | Kind | Required scope |
| --- | --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | secret | Cloudflare account identifier |
| `CLOUDFLARE_API_TOKEN` | secret | Workers Scripts edit for the new temporary account; no zone or DNS permission |

`cloudflare-production` contains the same names backed by a **different API
token**, restricted to the production Worker as narrowly as Cloudflare
permits. Neither token may administer zones or unrelated account
products. GitHub environment secrets are not exposed to pull-request checks.

Protected `main` is the human promotion decision. The production job has no
second manual approval, so an approved merge automatically reaches staging and
then production only after the smoke gate succeeds. Deployment workflow
concurrency is intentionally latest-main-wins: skipped intermediate commits
are already ancestors of the latest tested commit.

The security boundary and evidence required before intake is enabled are in
[`docs/intake-threat-model.md`](docs/intake-threat-model.md).
The decision register and copy/pasteable bootstrap sequence are in
[`docs/overhaul-rollout-runbook.md`](docs/overhaul-rollout-runbook.md).

## Worker secrets and GitHub state access

Each Worker environment has a distinct Wrangler secret:

| Secret | Environment | Purpose | Minimum GitHub reach |
| --- | --- | --- | --- |
| `GITHUB_STATE_TOKEN` | staging | Atomically append staging events | `lean-eval-state-staging`, Contents write and Metadata read |
| `GITHUB_STATE_TOKEN` | production | Atomically append production events | `lean-eval-state`, Contents write and Metadata read |
| `READINESS_TOKEN` | staging | Authenticate operational readiness probes | No GitHub access |
| `READINESS_TOKEN` | production | Authenticate operational readiness probes | No GitHub access |
| `AUTH_TOKEN_SECRET` | staging | HMAC-sign OAuth state, sessions, grants, and agent challenges | No GitHub access; random >=32-byte value |
| `AUTH_TOKEN_SECRET` | production | HMAC-sign OAuth state, sessions, grants, and agent challenges | No GitHub access; distinct from staging |
| `GITHUB_OAUTH_CLIENT_ID` / `GITHUB_OAUTH_CLIENT_SECRET` | each | Environment-specific GitHub OAuth application | `read:user` only; callback listed below |
| `GITHUB_VERIFICATION_TOKEN` | each | **LOCAL CONTRACT ONLY; not approved for production** source visibility/tag/gist verification | Unprovisioned pending broker/App decision |
| `GITHUB_DISPATCH_TOKEN` | each | **LOCAL CONTRACT ONLY; not approved for production** exact-ref workflow dispatch | Unprovisioned pending broker/App decision |

`DISPATCH_WORKFLOW_REF` must stay absent until an operator creates an immutable
tag named `lean-eval-dispatch/<40-character-commit>` at the reviewed workflow
commit. `deploy-worker.yml` owns creation: after checks, its
`promote-dispatch-ref` job enters the reviewer-gated
`submission-dispatch-promotion` environment and uses only the job-scoped
`GITHUB_TOKEN` with `contents: write`. A 32-byte lowercase-hex
`DISPATCH_PROMOTION_APPROVAL_GUARD` secret must exist only in that environment;
it has no external authority and makes missing/unprotected auto-created
environment configuration fail before tag creation. Existing tags are accepted only when
they resolve to the same SHA; collisions and failed read-back stop deployment.
A repository ruleset must target `lean-eval-dispatch/*`, allow creation, and
reject updates and deletion, without a bypass for the Worker, deployment
token, dispatch broker, or ordinary maintainers. The promotion output is
passed directly to both Wrangler deploy commands; rollback verifies the saved
binding and tag. Record the environment reviewers, tag, commit, ruleset
identifier, administrators, and failed update/delete drill here before
provisioning dispatch credentials. The Worker rejects a branch name, raw SHA,
or differently named tag with `503`.

The initial implementation uses separate, organization-owned fine-grained
personal access tokens because a GitHub App installation token expires after
about one hour and the Worker does not yet mint replacements. Tokens must be
scoped to one State repository, expire in at most 90 days, and be rotated
independently. Migrating to GitHub Apps requires a reviewed broker or in-Worker
JWT exchange and must not store an expiring installation token as a static
Worker secret.

Temporary OAuth callback URLs are exactly
`https://lean-eval-submission-server-staging.lean-eval.workers.dev/api/v1/oauth/callback`
and
`https://lean-eval-submission-server.lean-eval.workers.dev/api/v1/oauth/callback`.
If OAuth testing is separately authorized before migration, require distinct
OAuth Apps requesting only `read:user`. Replace both Apps or their exact
callbacks with the reviewed `lean-lang.org` URLs during migration; wildcard or
multi-environment callbacks are forbidden.

The production source-verification and dispatch credential mechanism remains
an explicit product/security decision. A narrowly scoped token broker reached
through a Cloudflare service binding is recommended: one operation verifies
repository visibility/tag/gist metadata, and one dispatches the pinned
workflow. The alternative is reviewed in-Worker GitHub App JWT signing and
installation-token refresh. Do not provision the static local-contract token
hooks, do not grant browser OAuth broad `repo` scope, and do not enable intake
until one design is selected, implemented, rotated, and recorded here.

The Worker owns durable dispatch reconciliation independently of the credential
choice. The intake CAS writes the immutable event batch, a validated targeted
submission view, and a per-submission dispatch outbox together. A successful
dispatch updates the view and deletes the outbox; a failed attempt records a
  bounded retry and the one-minute Cron Trigger visits one uniformly distributed
  UUIDv7-tail shard and
at most 20 due entries. The State validator checks view/outbox paths, shapes,
event references, ownership, and consistency. Workflow concurrency is keyed by
submission UUID, and deterministic result/State identities remain the final
duplicate-record guard. The selected broker supplies dispatch authorization;
it is not the persistence mechanism. Archive-locator consumption remains a
separate launch gate and must correlate `archive_path` to the UUID.

| Field | Staging | Production |
| --- | --- | --- |
| Credential type | Fine-grained PAT | Fine-grained PAT |
| Machine owner | Kim Morrison | Kim Morrison |
| Credential owner | Kim Morrison | Kim Morrison |
| Created / expires | **TO BE RECORDED AT CREATION; <=90 DAYS** | **TO BE RECORDED AT CREATION; <=90 DAYS** |
| Rotation owner / deadline | Kim Morrison / >=14 days before expiry | Kim Morrison / >=14 days before expiry |
| Last rotation drill | **TO BE RECORDED** | **TO BE RECORDED** |
| Replacement gate | GitHub App/broker before production intake | GitHub App/broker before production intake |

The internet-facing Worker token must not write workflow files, modify
repository settings, reach `lean-eval-submissions`, or reach the other
environment's State. State writes use the Git Data API and a non-forced update
of `refs/heads/main`; repository administration is not needed at runtime.

## State repository controls

Both state repositories are private operational ledgers. Each immutable event
occupies exactly one file under `events/<id-prefix>/<event-id>.json`. Configure:

- default branch `main`;
- deletion and force-push protection;
- required pull request and status checks for human-authored changes;
- no broad Actions write token;
- a ruleset bypass limited to the environment-specific state-writer principal;
- secret scanning and dependency alerts;
- validation of append-only history on pull requests and direct writer pushes;
- scheduled validation of the whole event tree, alerting, and an off-platform backup;
- audit-log review after credential rotation or unexplained ref contention.

Record ruleset IDs and backup destination after creation:

| Field | Staging | Production |
| --- | --- | --- |
| Branch ruleset ID | **TO BE RECORDED** | **TO BE RECORDED** |
| Backup destination | **TO BE RECORDED** | **TO BE RECORDED** |
| Restore drill date | **TO BE RECORDED** | **TO BE RECORDED** |

## Encrypted replay boundary

The selected root-key platform is AWS KMS in a new dedicated AWS account. No
AWS resource has been created yet:

| Field | Recorded value |
| --- | --- |
| AWS account purpose | Lean Eval archive-envelope root and audit only |
| AWS account ID | **TO BE RECORDED AFTER CREATION** |
| Root/contact email | **TO BE RECORDED; NEVER A WORKLOAD CREDENTIAL** |
| Billing owner | Kim Morrison (temporary) |
| Primary administrator | Kim Morrison |
| Provider-loss recovery | None by design; planned migration requires the active provider |
| KMS region | **TO BE DECIDED** |
| KMS key ARN / alias | **TO BE PROVISIONED AFTER D6** |
| Workload role | **TO BE PROVISIONED WITH THE KEY** |

AWS is an initial provider, not a stable protocol dependency. Archives remain
standard `age` ciphertext. Each archive has a small provider-neutral envelope
containing its submission ID, ciphertext digest, recipient, adapter name, and
opaque wrapped identity.

AWS account IDs, regions, KMS ARNs, encryption-context details, and SDK types
belong only to the AWS adapter payload and infrastructure ledger. They must not
enter archive paths, result IDs, replay IDs, stable capability claims, or the
generic envelope API. Replay and release consumers use a narrow wrap/unwrap
adapter; provider-specific SDK calls stay inside that adapter. While AWS is
available, migration consists of unwrapping each identity with the AWS adapter
and wrapping it with the replacement adapter. Archives and stable IDs do not
change. If AWS is already permanently unavailable, recovery is not supported.

Submission source archives remain encrypted outside the evaluation job. Replay
uses a Lean-Eval-owned controller through a provider-neutral disposable-executor
interface. No pre-existing project infrastructure or shared runner is part of
the trust boundary. For each
task the selected backend creates a fresh isolated instance, gives it one
single-submission decryption capability, and destroys it after the job without
a persistent workspace. The concrete backend remains a separate reviewed
decision and can be replaced without changing State, archive, request, or
verdict contracts.

Selection of a local hypervisor, hosted VM API, sandbox service, or other
implementation is deferred; no existing project runner is the default.

New UUIDv7 intakes must archive ciphertext at
`archives/<first-two-submission-UUID-hex>/<submission-UUID>.tar.age`. The
archive writer must record the repository, final Git commit, exact path, and
SHA-256 of the stored ciphertext bytes in State. `archive_submission.py`
implements that UUIDv7 mode while preserving the issue-derived legacy layout;
it emits a versioned State-locator handoff only after reading the ciphertext
back at the recorded immutable commit and verifying its digest. Wiring the
server pipeline to that mode, appending the causally linked `archive.completed`
event, and completing the decrypt/bundle-linkage drill remain launch gates.

## Public releases

`leanprover/lean-eval-releases` owns public two-month-delayed source bundles,
checksums, provenance manifests, signatures, and release notes. It does not
receive Worker or State write credentials. Embargo expiry is two UTC calendar
months after acceptance, not a fixed day count. The release workflow must
recompute eligibility from State and refuse early publication.

The exact license wording and the interaction with contributor rights are a
separate legal/documentation review gate. No release job is enabled before that
text is approved.

Release validation takes its acceptance timestamp and immutable archive
repository, commit, canonical path, and ciphertext digest from a trusted State
snapshot. It receives the publication time from the workflow rather than the
proposed manifest and hashes regular bundle files beneath the release root.
Self-declared clocks, symlinks, provenance mismatches, and unverified bundle
digests are rejected.

## Monitoring and recovery

Minimum launch monitors:

- Worker exception and non-2xx rate split by environment;
- staging and production `/healthz` synthetic checks;
- authenticated `/readyz` alerting once intake is enabled; readiness responses
  are briefly cached to protect the GitHub API dependency;
- GitHub API rate-limit and ref-contention alerts;
- Worker `rate_limited` responses and dispatch-outbox age/attempt alerts;
- State tree validation and backup freshness;
- deployment failure notifications;
- replay VM creation, destruction, and capability-expiry audit events;
- delayed-release eligibility and publication failures.

Rollback changes only Worker code/configuration. It does not revert GitHub
State or other resources. Use the manual
[`rollback-worker.yml`](.github/workflows/rollback-worker.yml) workflow with a
reviewed version ID and the commit marker expected from that version. It runs
under the protected production environment, performs a noninteractive Wrangler
rollback, and verifies the complete health payload. Record the incident and
version IDs here. Never rewrite State to match an older Worker; deploy a
compatibility fix or append a corrective event.

| Drill / incident | Date | Result / link |
| --- | --- | --- |
| Staging deploy and smoke | **PENDING** | **TO BE RECORDED** |
| Production deploy and smoke | **PENDING** | **TO BE RECORDED** |
| Worker rollback | **PENDING** | **TO BE RECORDED** |
| State restore | **PENDING** | **TO BE RECORDED** |
| Replay decrypt and destruction | **PENDING** | **TO BE RECORDED** |
| Release reconstruction | **PENDING** | **TO BE RECORDED** |

## Reconciliation checklist

At least quarterly, and after every infrastructure change:

1. compare Cloudflare Worker names, domains, routes, compatibility settings,
   observability, and secrets metadata to this file and `wrangler.jsonc`;
2. compare GitHub environments, secret names, credentials, permissions,
   repository visibility, rulesets, and runner labels to this file;
3. verify staging cannot reach production State and vice versa;
4. rotate one non-production credential and complete a staging deploy;
5. test rollback and State restore procedures;
6. update `Last reconciled`, owners, identifiers, dates, and drill results here.
