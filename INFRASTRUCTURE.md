# lean-eval infrastructure inventory

This file is the source of truth for externally hosted lean-eval
infrastructure. A change to Cloudflare, GitHub credentials, deployment credentials,
state repositories, runner topology, DNS, or release storage is incomplete
until this ledger changes in the same pull request or an immediately linked
operations pull request. Secret **names, owners, scopes, and rotation dates**
belong here; secret values do not.

Last reconciled: 2026-08-21 (deployment tokens, State-writer tokens, browser
OAuth Apps, and the two broker GitHub Apps provisioned; intake still disabled).
Temporary owner: Kim Morrison. Target owner: leanprover organization
administrators. Service code:
[`server/`](server/).

## Provisioning status

| Resource | Desired identifier | Environment | Status |
| --- | --- | --- | --- |
| Cloudflare account | `lean-eval` (`a46b90978a1c29cc4795f30677e7e4b8`) | temporary dedicated | **PROVISIONED 2026-08-20** |
| Cloudflare Worker | `lean-eval-submission-server-staging` | staging | **PROVISIONED 2026-08-20; INTAKE DISABLED** |
| Cloudflare Worker | `lean-eval-submission-server` | production | **PROVISIONED 2026-08-20; INTAKE DISABLED** |
| Private GitHub broker Worker | `lean-eval-github-broker-staging` | staging | **PROVISIONED 2026-08-20; APP SECRETS INSTALLED 2026-08-21** |
| Private GitHub broker Worker | `lean-eval-github-broker-production` | production | **PROVISIONED 2026-08-20; APP SECRETS INSTALLED 2026-08-21** |
| Temporary Worker route | `lean-eval-submission-server-staging.lean-eval.workers.dev` | staging | **ACTIVE 2026-08-20; INTAKE DISABLED** |
| Temporary Worker route | `lean-eval-submission-server.lean-eval.workers.dev` | production | **ACTIVE 2026-08-20; INTAKE DISABLED** |
| Target Worker custom domain | `eval-submit-staging.lean-lang.org` | staging | **DEFERRED; ZONE ABSENT** |
| Target Worker custom domain | `eval-submit.lean-lang.org` | production | **DEFERRED; ZONE ABSENT** |
| GitHub state repository | `leanprover/lean-eval-state-staging` | staging | **CREATED PRIVATE 2026-08-20** |
| GitHub state repository | `leanprover/lean-eval-state` | production | **CREATED PRIVATE 2026-08-20** |
| GitHub generator repository | `leanprover/lean-eval-generator` | shared | **CREATED PUBLIC 2026-08-20** |
| GitHub release repository | `leanprover/lean-eval-releases` | production | **CREATED PUBLIC 2026-08-20; PUBLICATION DISABLED** |
| GitHub Environment | `cloudflare-staging` (`20259250422`) | staging | **CREATED 2026-08-20; ACCOUNT ID SET; API TOKEN SET 2026-08-21** |
| GitHub Environment | `cloudflare-production` (`20259250928`) | production | **CREATED 2026-08-20; ACCOUNT ID SET; API TOKEN SET 2026-08-21** |
| GitHub Environment | `submission-dispatch-promotion` (`20259251430`) | shared | **CREATED 2026-08-20; REVIEW + GUARD CONFIGURED** |
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
| Additional administrator | none; optional for the temporary account |

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
- a private `GITHUB_BROKER` service binding in each environment; the broker
  Workers have no public route and hold the two D9 App private keys;
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

The intake-disabled bootstrap was performed manually with Wrangler OAuth. The
dedicated deployment tokens are now installed. The latest pre-merge versions,
including the versions created when secrets were installed, all use exact
commit `9f5db319309bfc3f4a38215fba71e4763228c2a6`:

| Environment | Private broker version | Intake Worker version | Health verification |
| --- | --- | --- | --- |
| staging | `eee1ee9f-71d2-40aa-9daf-950a991b7b58` | `4b363f1d-e4ed-491a-8009-ea4cf45904c8` | environment `staging`, intake `false`, exact commit |
| production | `1d9cfd9f-cdf6-4a65-8e62-ad9f7299dd7f` | `a30535bd-8ec6-4de3-b2b9-34aead7434fa` | environment `production`, intake `false`, exact commit |

This manual bootstrap does not replace deployment automation.
`CLOUDFLARE_ACCOUNT_ID` and a distinct, narrowly scoped
`CLOUDFLARE_API_TOKEN` are installed in each Cloudflare environment. The first
post-merge deployment is the functional verification of those opaque tokens.

The namespace IDs are user-defined positive integers and must remain unique in
the Cloudflare account; bindings with the same ID share counters. Configuration
and locality semantics follow Cloudflare's
[Rate Limiting binding documentation](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/).
Cron Triggers are managed only through Wrangler as documented by
[Cloudflare](https://developers.cloudflare.com/workers/configuration/cron-triggers/).

The temporary `workers.dev` endpoints are public and are not the permanent
hostname design. They exist for the intake-disabled bootstrap. The temporary
account has no `lean-lang.org` zone. A later provider or account migration uses
the documented Worker bindings and broker protocol; it does not change stable
submission IDs, State events, archive locators, or the public API.

## Deployment automation

[`deploy-worker.yml`](.github/workflows/deploy-worker.yml) is the only normal
deployment path. A change under `server/` merged to protected `main` runs:

1. locked dependency install, generated binding types, typecheck, lint, tests,
   dependency audit, and Wrangler dry run;
2. staging broker deploy, intake Worker deploy, and `GET /healthz` smoke test;
3. production broker deploy, intake Worker deploy, and `GET /healthz` smoke
   test.

GitHub environment `cloudflare-staging` must contain:

| Name | Kind | Required scope |
| --- | --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | secret | Cloudflare account identifier |
| `CLOUDFLARE_API_TOKEN` | secret | Workers Scripts edit for the dedicated Lean Eval account; no zone or DNS permission |

`cloudflare-production` contains the same names backed by a **different API
token**. Cloudflare's Workers Scripts permission is account-scoped, not
script-scoped, so both tokens can edit the four Workers in this dedicated Lean
Eval account. Environment separation still gives independent revocation and
rotation, while the dedicated account keeps unrelated services outside that
authority. Neither token may administer zones or other account products.
GitHub environment secrets are not exposed to pull-request checks.

Recorded deployment tokens, both account-owned in the `lean-eval` account:

| Token name | Environment | Permission | Created | Expiry |
| --- | --- | --- | --- | --- |
| `lean-eval-deploy-staging` | `cloudflare-staging` | Workers Scripts: Edit, entire `lean-eval` account | 2026-08-21 | none |
| `lean-eval-deploy-production` | `cloudflare-production` | Workers Scripts: Edit, entire `lean-eval` account | 2026-08-21 | none |

Neither token carries any other permission. Each was checked at creation
against `/accounts/<id>/tokens/verify` (active), `/accounts/<id>/workers/scripts`
(the four Lean Eval Workers, and nothing else), `/zones` (zero zones), and
`/accounts/<id>/storage/kv/namespaces` (denied). They carry no expiry so an
unattended deployment cannot fail on a lapsed credential; rotation is therefore
manual and owned by Kim Morrison. See
Cloudflare's [GitHub Actions authentication
guide](https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/)
and [permission reference](https://developers.cloudflare.com/fundamentals/api/reference/permissions/).

Both Cloudflare environments are restricted to protected branches. The
`submission-dispatch-promotion` environment requires review by `kim-em`, is
restricted to protected branches, and contains the environment-only
`DISPATCH_PROMOTION_APPROVAL_GUARD`. Dispatch tags are protected by active tag
ruleset `21094118` (`Protect Lean Eval dispatch tags`), which rejects updates
and deletion of `refs/tags/lean-eval-dispatch/*` without a bypass. No dispatch
tag is created until the reviewed workflow reaches protected `main`.

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
| `GITHUB_OAUTH_CLIENT_ID` / `GITHUB_OAUTH_CLIENT_SECRET` | each | Environment-specific GitHub OAuth application | `read:user` only; callback listed below; **SET 2026-08-21** |
| `GITHUB_VERIFICATION_TOKEN` | each | **LOCAL CONTRACT ONLY; not approved for production** source visibility/tag/gist verification | Intentionally absent; source broker provisioned |
| `GITHUB_DISPATCH_TOKEN` | each | **LOCAL CONTRACT ONLY; not approved for production** exact-ref workflow dispatch | Intentionally absent; dispatch broker provisioned |

Each private broker environment instead receives four Wrangler secrets:

| Secret | Purpose | Status |
| --- | --- | --- |
| `SOURCE_APP_ID` | Source-reader GitHub App identifier | **SET 2026-08-21**; App `4666604` |
| `SOURCE_APP_PRIVATE_KEY` | Mint repository-scoped source-reader installation tokens | **SET 2026-08-21**; key `4176146`, the App's only key |
| `DISPATCH_APP_ID` | Workflow-dispatch GitHub App identifier | **SET 2026-08-21**; App `4666633` |
| `DISPATCH_APP_PRIVATE_KEY` | Mint a token scoped to `leanprover/lean-eval-submissions` | **SET 2026-08-21**; key `4176163`, the App's only key |

Recorded GitHub Apps:

| App | Slug | App ID | Permissions | Installation |
| --- | --- | --- | --- | --- |
| Lean Eval Source Reader | `lean-eval-source-reader` | `4666604` | Metadata read, Contents read | none; installed per contributor repository on opt-in |
| Lean Eval Workflow Dispatcher | `lean-eval-workflow-dispatcher` | `4666633` | Metadata read, Contents read, Actions read/write | `155329316` on `leanprover`, repository selection `selected`, exactly `leanprover/lean-eval-submissions` |

Both Apps are owned by the personal account `kim-em` because the temporary
owner is not an administrator of the `leanprover` organization. Transfer the
existing registrations to `leanprover` before production intake, then verify
the App IDs, private-key authentication, and installation selection before
assuming that no Worker secret rotation is required. Neither App subscribes to
any event and neither has a webhook. The source reader deliberately has no
Actions permission. The dispatcher installation was verified on 2026-08-21 by
minting an installation token and listing `/installation/repositories`, which
returned exactly one repository.

Recorded browser OAuth Apps, both owned by the personal account `kim-em` for
the same reason as the two GitHub Apps:

| App name | Application ID | Client ID | Callback |
| --- | --- | --- | --- |
| Lean Eval Submissions (staging) | `3806355` | `Ov23li6zjHADKyrgKeRa` | `https://lean-eval-submission-server-staging.lean-eval.workers.dev/api/v1/oauth/callback` |
| Lean Eval Submissions | `3806359` | `Ov23liFcOLHsyvY9DmQ5` | `https://lean-eval-submission-server.lean-eval.workers.dev/api/v1/oauth/callback` |

Each App has one exact callback with strict matching, no wildcard, device flow
disabled, and user access token expiry enabled. Each callback was compared byte
for byte against the matching `OAUTH_CALLBACK_URL` in `server/wrangler.jsonc`.
The Worker requests `scope=read:user` and nothing else
(`server/src/app.ts`). Client secrets were generated once per App and installed
directly into the matching Worker.

`READINESS_TOKEN` and `AUTH_TOKEN_SECRET` were installed with distinct random
values in both Workers on 2026-08-20. The matching readiness value is also an
environment secret for future authenticated probes. As of 2026-08-21 the State,
OAuth, and broker App credentials are installed, while the two State-writer
tokens await `leanprover` owner approval and therefore still carry no authority.
`GITHUB_VERIFICATION_TOKEN` and `GITHUB_DISPATCH_TOKEN` remain deliberately
unprovisioned. `INTAKE_ENABLED` stays false, so no intake route can exercise
any of these credentials.

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
identifier, and administrators here before provisioning dispatch credentials.
The Worker rejects a branch name, raw SHA,
or differently named tag with `503`.

The protected promotion environment is `submission-dispatch-promotion`
(`20259251430`) with reviewer `kim-em`. Active tag ruleset `21094118` targets
`refs/tags/lean-eval-dispatch/*`, rejects updates and deletion, and has no
bypass. No dispatch tag exists before the first reviewed post-merge deployment;
record its exact tag and commit after that deployment.

The approved D9 design uses separate organization-owned GitHub Apps for source
verification and workflow dispatch, reached through a narrow token broker.
The D5 intake-disabled State bootstrap uses separate, single-repository
fine-grained personal access tokens; those are not substitutes for either D9
App. Installation tokens expire after about one hour and must not be stored as
long-lived Worker secrets.

Temporary OAuth callback URLs are exactly
`https://lean-eval-submission-server-staging.lean-eval.workers.dev/api/v1/oauth/callback`
and
`https://lean-eval-submission-server.lean-eval.workers.dev/api/v1/oauth/callback`.
If OAuth testing is separately authorized before migration, require distinct
OAuth Apps requesting only `read:user`. Replace both Apps or their exact
callbacks with the reviewed `lean-lang.org` URLs during migration; wildcard or
multi-environment callbacks are forbidden.

The production source-verification and dispatch mechanism is the implemented
private Cloudflare service-binding broker. One App can read repository
metadata and tags; the other can dispatch only the pinned workflow in
`leanprover/lean-eval-submissions`. The broker validates an exact v1 audience,
authority, operation, repository, and immutable commit, mints scoped
installation tokens, and never returns them to intake. This internal protocol
is the provider seam: another provider can replace the broker without changing
public API, State, archive, or result identifiers. Do not provision the static
local-contract token hooks and do not grant browser OAuth broad `repo` scope.

Installation tokens cannot read submitter-owned private gists. The existing
headless-agent gist proof therefore remains disabled until it is replaced with
an App-verifiable proof or a separate user-token design is approved. Browser
intake is unaffected. Do not expand either installation App merely to bypass
this limit.

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
| Credential type | Fine-grained PAT bootstrap | Fine-grained PAT bootstrap |
| Machine owner | Kim Morrison | Kim Morrison |
| Credential owner | Kim Morrison | Kim Morrison |
| Token name | `lean-eval-state-writer-staging` (`18528992`) | `lean-eval-state-writer-production` (`18529041`) |
| Created / expires | 2026-08-21 / 2026-11-19 (90 days) | 2026-08-21 / 2026-11-19 (90 days) |
| Approval state | **PENDING `leanprover` owner approval** | **PENDING `leanprover` owner approval** |
| Rotation owner / deadline | Kim Morrison / by 2026-11-05 | Kim Morrison / by 2026-11-05 |
| Intake gate | D9 Apps/broker provisioned; PAT approved and matching bypass tested | D9 Apps/broker provisioned; PAT approved and matching bypass tested |

Each token grants Metadata read and Contents read/write on exactly one
repository and holds no Actions, Administration, Workflows, or Issues
permission. Because the temporary owner is an organization member rather than
an administrator, GitHub issued both tokens in `pending` state; an owner of
`leanprover` must approve them at
`https://github.com/organizations/leanprover/settings/personal-access-token-requests`
before either can reach its State repository. GitHub reveals a fine-grained
token value only once, so both values were installed into their matching Worker
at creation and start working when approval lands. Until then each Worker holds
a credential with no authority, which is safe while `INTAKE_ENABLED` is false.
Fine-grained tokens can always read public repositories, so a read of the public
`lean-eval-submissions` is inherent to the credential type and is not a grant
made here.

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
- the only writer bypass is the specific `kim-em` user required by the two
  matching, single-repository bootstrap PATs; remove it if those PATs retire;
- secret scanning and dependency alerts;
- validation of append-only history on pull requests and direct writer pushes;
- scheduled validation of the whole event tree.

Recorded repository controls:

| Field | Staging | Production |
| --- | --- | --- |
| Branch ruleset ID | `21094006` | `21094005` |
| Writer bypass | `kim-em` (`User` 477956), always | `kim-em` (`User` 477956), always |

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
server pipeline to that mode and appending the causally linked
`archive.completed` event remain launch gates.

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

## Recovery

Rollback changes only Worker code/configuration. It does not revert GitHub
State or other resources. Use the manual
[`rollback-worker.yml`](.github/workflows/rollback-worker.yml) workflow with a
reviewed version ID and the commit marker expected from that version. It runs
under the protected production environment, performs a noninteractive Wrangler
rollback, and verifies the complete health payload. Record the incident and
version IDs here. Never rewrite State to match an older Worker; deploy a
compatibility fix or append a corrective event.

| Verification / incident | Date | Result / link |
| --- | --- | --- |
| Staging deploy and smoke | 2026-08-20 | broker `073e7532-d86d-4dad-9280-f413ff970dab`, intake `0669c5a2-9cf6-445c-98ce-dacab8f72af0`; exact commit and intake-disabled assertions passed |
| Production deploy and smoke | 2026-08-20 | broker `b658a77b-f7e8-4bd2-9268-a7862af07122`, intake `759d75ab-971d-4b93-ba69-ce0ac5319e04`; exact commit and intake-disabled assertions passed |
| Readiness-secret rotation | 2026-08-20 | distinct staging/production values rotated in both Workers and matching protected GitHub environment secrets; resulting intake versions `92aa9ac5-4305-47ab-85f2-8495c6935123` / `46ef4231-1ace-4343-9f16-ce9ea60e194e`; health remained commit-exact and intake-disabled |
| Deployment token provisioning | 2026-08-21 | `lean-eval-deploy-staging` and `lean-eval-deploy-production` created with Workers Scripts: Edit only; each verified active, reaching the four Lean Eval Workers, zero zones, KV denied; installed in the matching protected GitHub environment |
| State-writer token provisioning | 2026-08-21 | two single-repository fine-grained tokens created, expiring 2026-11-19, installed as `GITHUB_STATE_TOKEN` in the matching Worker; both pending `leanprover` owner approval |
| State ruleset bypasses | 2026-08-21 | `kim-em` (`User` 477956) is the sole always-allowed bypass actor on staging ruleset `21094006` and production ruleset `21094005`; all protection rules otherwise unchanged |
| Browser OAuth App provisioning | 2026-08-21 | two Apps with exact per-environment callbacks; client ID and secret installed in the matching Worker |
| Broker GitHub App provisioning | 2026-08-21 | source reader `4666604` (Metadata read, Contents read, no installation) and workflow dispatcher `4666633` (Metadata read, Contents read, Actions read/write, installation `155329316` limited to `leanprover/lean-eval-submissions`); four secrets installed in both broker environments |
| Post-provisioning health check | 2026-08-21 | both Workers report `status ok`, commit `9f5db319309bfc3f4a38215fba71e4763228c2a6`, correct environment, and `intake_enabled false` |
| Worker rollback | not run | Use only if an actual deployment needs rollback |
| Replay decrypt and destruction | blocked | D6 key/provider work intentionally not provisioned |
| Release reconstruction | blocked | Publication remains disabled |

## Reconciliation checklist

At least quarterly, and after every infrastructure change:

1. compare Cloudflare Worker names, domains, routes, compatibility settings,
   observability, and secrets metadata to this file and `wrangler.jsonc`;
2. compare GitHub environments, secret names, credentials, permissions,
   repository visibility, rulesets, and runner labels to this file;
3. verify staging cannot reach production State and vice versa;
4. update `Last reconciled`, owners, identifiers, dates, and deployment results here.
