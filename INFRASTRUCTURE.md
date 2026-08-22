# lean-eval infrastructure inventory

This file is the source of truth for externally hosted lean-eval
infrastructure. A change to Cloudflare, GitHub credentials, deployment credentials,
state repositories, runner topology, DNS, or release storage is incomplete
until this ledger changes in the same pull request or an immediately linked
operations pull request. Secret **names, owners, scopes, and rotation dates**
belong here; secret values do not.

Last reconciled: 2026-08-22 (Worker commit `a34b2053` deployed and
smoke-tested in both environments with intake disabled; deployment tokens,
State-writer tokens, browser OAuth Apps, and both broker GitHub Apps remain
provisioned and preflighted; no Lean Eval AWS account or resource exists, and
the six empty AWS workload environment shells and their exact ref policies are
recorded below).
Temporary owner: Kim Morrison. Target owner: leanprover organization
administrators. Service code:
[`server/`](server/).

## Provisioning status

| Resource | Desired identifier | Environment | Status |
| --- | --- | --- | --- |
| Cloudflare account | `lean-eval` (`a46b90978a1c29cc4795f30677e7e4b8`) | temporary dedicated | **PROVISIONED 2026-08-20** |
| Cloudflare Worker | `lean-eval-submission-server-staging` | staging | **PROVISIONED 2026-08-20; INTAKE DISABLED AFTER E2E FIXTURE** |
| Cloudflare Worker | `lean-eval-submission-server` | production | **PROVISIONED 2026-08-20; INTAKE DISABLED** |
| Private GitHub broker Worker | `lean-eval-github-broker-staging` | staging | **PROVISIONED 2026-08-20; APP SECRETS INSTALLED 2026-08-21** |
| Private GitHub broker Worker | `lean-eval-github-broker-production` | production | **PROVISIONED 2026-08-20; APP SECRETS INSTALLED 2026-08-21** |
| Temporary Worker route | `lean-eval-submission-server-staging.lean-eval.workers.dev` | staging | **ACTIVE 2026-08-20; INTAKE DISABLED AFTER E2E FIXTURE** |
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
| GitHub Environment | `archive-staging` (`EN_kwDOSh7OzM8AAAAEu8r2_A`) | staging archive | **CREATED 2026-08-21; TAG POLICY SET; ROLE ARN NOT SET** |
| GitHub Environment | `archive-production` (`EN_kwDOSh7OzM8AAAAEu8r25w`) | production archive | **CREATED 2026-08-21; TAG POLICY SET; ROLE ARN NOT SET** |
| GitHub Environment | `replay-staging` (`EN_kwDOSh7OzM8AAAAEu8r21Q`) | staging replay | **CREATED 2026-08-21; MAIN + DISPATCH TAG POLICIES SET 2026-08-22; ROLE ARN NOT SET** |
| GitHub Environment | `replay-production` (`EN_kwDOSh7OzM8AAAAEu8r3MQ`) | production replay | **CREATED 2026-08-21; PROTECTED BRANCHES ONLY; ROLE ARN NOT SET** |
| GitHub Environment | `release-staging` (`EN_kwDOT-oWes8AAAAEu8r3Mw`) | staging release | **CREATED 2026-08-21; PROTECTED BRANCHES ONLY; ROLE ARN NOT SET** |
| GitHub Environment | `release-production` (`EN_kwDOT-oWes8AAAAEu8r3KQ`) | production release | **CREATED 2026-08-21; PROTECTED BRANCHES ONLY; ROLE ARN NOT SET** |
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
dedicated deployment tokens are installed and exercised by every normal
deployment. Current versions use exact commit
`a34b2053ce8c4e7e9833d57de893ab2aa62e797b`:

| Environment | Private broker version | Intake Worker version | Health verification |
| --- | --- | --- | --- |
| staging | `fa8deb74-db7c-4bb5-96ee-566397a9fdf6` | `9707b29b-b67d-49dc-93e9-d32aa530c7f5` | environment `staging`, intake `false`, exact commit |
| production | `5684fc28-b3ca-4c60-9992-e79d2f8bd576` | `3d3d9c54-4552-4a46-a1ee-6a5ec6aea5e8` | environment `production`, intake `false`, exact commit |

This manual bootstrap does not replace deployment automation.
`CLOUDFLARE_ACCOUNT_ID` and a distinct, narrowly scoped
`CLOUDFLARE_API_TOKEN` are installed in each Cloudflare environment. The
2026-08-21 post-merge deployment verified both opaque tokens successfully.

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
deployment path. A change to the Worker, submission workflow, trusted workflow
scripts, or archive recipient set merged to protected `main` runs:

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

Staging intake state is changed only through the protected, manual
`Set staging intake` workflow. The operator must select `main`, provide the
exact current protected-main commit, and choose `enabled` or `disabled`. The
workflow requires that commit's immutable `lean-eval-dispatch/<commit>` tag,
deploys only the staging intake Worker, and verifies the resulting structured
health response. It cannot target production. Any later ordinary main
deployment returns staging to the tracked safe default `INTAKE_ENABLED=false`;
rerun the manual workflow after reviewing the newly deployed commit if staging
testing should continue.

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
| `LIFECYCLE_CALLBACK_TOKEN` | staging | Authenticate the source-free post-archive State callback job | No GitHub access; distinct random value also stored only in `cloudflare-staging` |
| `LIFECYCLE_CALLBACK_TOKEN` | production | Authenticate the source-free post-archive State callback job | No GitHub access; distinct random value also stored only in `cloudflare-production` |
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

Both registrations publicly report the `leanprover` organization as owner.
The 2026-08-21 ownership transfers preserved App IDs `4666604` and `4666633`.
Neither App
subscribes to any event and neither has a webhook. The source reader
deliberately has no Actions permission. The dispatcher installation was
verified on 2026-08-21 by minting an installation token and listing
`/installation/repositories`, which returned exactly one repository.

Recorded browser OAuth Apps, both currently owned by the personal account
`kim-em`:

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
environment secret for authenticated probes. As of 2026-08-21 the State,
OAuth, and broker App credentials are installed, and both State-writer tokens
are approved and preflighted. `GITHUB_VERIFICATION_TOKEN` and
`GITHUB_DISPATCH_TOKEN` remain deliberately unprovisioned. Staging intake is
enabled only for the fixture exercise; production intake remains disabled.

The authenticated `POST /readyz` State-writer preflight is the exception to
that last sentence: it is an operator-only credential check that works while
intake remains disabled. It reads the State branch and submits a non-forced
same-commit ref update, proving both Contents-write authority and the configured
ruleset bypass without changing the branch, commit graph, or State tree.

The authenticated staging-only `POST /internal/v1/source-reader-preflight`
performs one repository-metadata read through the private Source Reader broker.
Its protected manual workflow fixes the target to
`kim-em/lean-eval-intake-fixture` and proves the transferred App private key,
installation selection, broker binding, and private visibility without
dispatching or evaluating a submission. Production rejects this preflight.

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
bypass. Immutable tag
`lean-eval-dispatch/a928be873db6569e2b4ccb3fb8b399d0f19b2e78`
resolves exactly to commit `a928be873db6569e2b4ccb3fb8b399d0f19b2e78`.

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
`leanprover/lean-eval-submissions`. The broker validates an exact
protocol-version-1 audience, authority, operation, repository, and immutable commit, mints scoped
installation tokens, and never returns them to intake. This internal protocol
is the provider seam: another provider can replace the broker without changing
public API, State, archive, or result identifiers. Do not provision the static
local-contract token hooks and do not grant browser OAuth broad `repo` scope.

GitHub [documents secret gists as unlisted, not
private](https://docs.github.com/en/get-started/writing-on-github/editing-and-sharing-content-with-gists/creating-gists):
an exact high-entropy gist ID is readable without authentication.
Headless-agent proof therefore fetches only
`GET /gists/<id>` anonymously and verifies the signed, expiring challenge in
`lean-eval-proof.txt`, `public: false`, and exact owner ID/login. It never sends
an App, OAuth, or State credential to the gist endpoint. The source-reader App
still verifies only repository metadata and the immutable source tag; do not
expand either App with user-level gist authority.

The Worker owns durable dispatch reconciliation independently of the credential
choice. The intake CAS writes the immutable event batch, a validated targeted
submission view, and a per-submission dispatch outbox together. A successful
dispatch updates the view and deletes the outbox; a failed attempt records a
bounded retry and the one-minute Cron Trigger visits one uniformly distributed
UUIDv7-tail shard and at most 20 due entries. The State validator checks
view/outbox paths, shapes,
event references, ownership, and consistency. Workflow concurrency is keyed by
submission UUID, and deterministic result/State identities remain the final
duplicate-record guard. The selected broker supplies dispatch authorization;
it is not the persistence mechanism. The archive job emits a strict completion
object only after reading the ciphertext back at its immutable commit and
verifying its digest. A separate source-free `archive_state` job holds only the
matching environment's callback token and sends that object to the Worker. The
Worker validates the UUID-derived path, authenticated environment, existing
dispatched submission, and exact payload before appending an idempotent
`archive.completed` event and atomically upgrading the targeted view to the
lifecycle-aware schema. A
successful `archive_state` acknowledgement is a dependency of evaluation, so
untrusted Lean cannot start before both durable audit persistence and State
recording. The evaluation job independently refetches the archived source
commit and fails closed unless its frozen metadata digest matches. A second
source-free callback job derives its fixed timestamp from the immutable
archive completion, distinguishes accepted/rejected/pipeline-failed outcomes,
and appends `evaluation.started` plus its terminal event in one CAS update.
If source fetch, size validation, encryption, or persistence prevents an
archive, a mutually exclusive source-free callback records the classified
`archive.failed` state instead of leaving the submission indefinitely pending.
No State credential or callback token enters the evaluation or archive job.

| Field | Staging | Production |
| --- | --- | --- |
| Credential type | Fine-grained PAT bootstrap | Fine-grained PAT bootstrap |
| Machine owner | Kim Morrison | Kim Morrison |
| Credential owner | Kim Morrison | Kim Morrison |
| Token name | `lean-eval-state-writer-staging` (`18528992`) | `lean-eval-state-writer-production` (`18529041`) |
| Created / expires | 2026-08-21 / 2026-11-19 (90 days) | 2026-08-21 / 2026-11-19 (90 days) |
| Approval state | **APPROVED and preflighted 2026-08-21** | **APPROVED and preflighted 2026-08-21** |
| Rotation owner / deadline | Kim Morrison / by 2026-11-05 | Kim Morrison / by 2026-11-05 |
| Intake gate | D9 Apps/broker provisioned; PAT approved and matching bypass tested | D9 Apps/broker provisioned; PAT approved and matching bypass tested |

Each token grants Metadata read and Contents read/write on exactly one
repository and holds no Actions, Administration, Workflows, or Issues
permission. Both organization approvals landed on 2026-08-21. Protected-main
workflow runs `32465890236` (staging) and `32465892118` (production) then proved
push authority and the matching ruleset bypass by non-forced same-commit ref
updates. The observed heads were respectively `6bfc9eb633c6c8bbaa2937708183d70fca7668fa`
and `89cdf9bd163f451fa51c7695c14388e11e1d609d`; neither tree changed and intake
remained disabled.
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

### Public State projection

Raw State and its internal `materialized/domain.json` remain private. Production
State PR `#4` (merge `e9477c7c88f71127bda3a7442d35068fd2d7a5dd`)
and its staging mirror `#4` (merge
`685f293dae9e64a32d5466211a06c9e6bc892a3b`) define the strict
`public-state-projection-v1` contract (schema version 1). It contains only
recorded results, public credit/production metadata, acceptance provenance, replay measurements,
release status, and released-solution links. It omits pending/rejected
submissions, submission IDs, source/archive locators, and authentication
nonces. Result identities are recomputed, metadata fields are closed, and the
artifact records its exact private State commit, canonical event digest, and
event count.

The lifecycle-aware leaderboard's production build reads State using deploy
key `160968617` (`lean-eval-leaderboard-public-projection`). The key is read-only
and scoped solely to `leanprover/lean-eval-state`; its private half exists only
as leaderboard Actions secret `PRODUCTION_STATE_READ_KEY` (set 2026-08-22).
Pull-request builds never receive it. Production generates and revalidates the
redacted artifact, verifies its commit against the checkout, and publishes the
exact bytes as `site-data/public-state.json`. It never publishes raw events or
the internal domain. Removing deploy key `160968617` immediately revokes this
read path without affecting State writers or intake.

## Encrypted replay boundary

The selected root-key platform is AWS KMS in a new dedicated AWS account. No
Lean Eval AWS account exists yet. The implementation and linted SAM template
exist, but no AWS resource has been created:

| Field | Recorded value |
| --- | --- |
| AWS account purpose | Lean Eval archive-envelope root and audit only |
| AWS account ID | **TO BE RECORDED AFTER CREATION** |
| Root/contact email | **TO BE RECORDED; NEVER A WORKLOAD CREDENTIAL** |
| Billing owner | Kim Morrison (temporary) |
| Primary administrator | Kim Morrison |
| Provider-loss recovery | None by design; planned migration requires the active provider |
| KMS region | `us-east-1` selected for the initial small service; the stable contract is region-neutral |
| Staging stack | `lean-eval-key-adapter-staging` — **TO BE PROVISIONED** |
| Production stack | `lean-eval-key-adapter-production` — **TO BE PROVISIONED** |
| KMS aliases | `alias/lean-eval-archive-identities-staging` and `-production` — **TO BE PROVISIONED** |
| One-use tables | `lean-eval-capability-consumption-staging` and `-production` — **TO BE PROVISIONED** |
| Unwrap functions | `lean-eval-archive-unwrap-staging` and `-production`, alias `live` — **TO BE PROVISIONED** |
| Archive roles | `lean-eval-archive-wrap-staging` and `-production` (KMS Encrypt only) — **TO BE PROVISIONED** |
| Replay controller roles | `lean-eval-replay-unwrap-invoker-staging` and `-production` in `lean-eval-submissions` (Lambda Invoke only) — **TO BE PROVISIONED** |
| Release controller roles | `lean-eval-release-unwrap-invoker-staging` and `-production` in `lean-eval-releases` (Lambda Invoke only) — **TO BE PROVISIONED** |
| Function roles | KMS Decrypt + conditional DynamoDB PutItem + logs only — **TO BE PROVISIONED** |

The GitHub environment shells were created before the AWS account so their ref
boundaries could be verified without granting AWS authority. They currently
contain no secrets and no variables:

| Repository | Environment | Environment node | Protection rule | Ref policy | Policy ID |
| --- | --- | --- | --- | --- | --- |
| `leanprover/lean-eval-submissions` | `archive-staging` | `EN_kwDOSh7OzM8AAAAEu8r2_A` | `63321649` | tag `lean-eval-dispatch/*` | `57914845` |
| `leanprover/lean-eval-submissions` | `archive-production` | `EN_kwDOSh7OzM8AAAAEu8r25w` | `63321647` | tag `lean-eval-dispatch/*` | `57914846` |
| `leanprover/lean-eval-submissions` | `replay-staging` | `EN_kwDOSh7OzM8AAAAEu8r21Q` | `63352004` | branch `main`; tag `lean-eval-dispatch/*` | `57941304`; `57941307` |
| `leanprover/lean-eval-submissions` | `replay-production` | `EN_kwDOSh7OzM8AAAAEu8r3MQ` | `63321654` | protected branches only | not applicable |
| `leanprover/lean-eval-releases` | `release-staging` | `EN_kwDOT-oWes8AAAAEu8r3Mw` | `63321653` | protected branches only | not applicable |
| `leanprover/lean-eval-releases` | `release-production` | `EN_kwDOT-oWes8AAAAEu8r3KQ` | `63321651` | protected branches only | not applicable |

The manual `public-replay-smoke.yml` job uses `replay-staging` but needs and
receives no environment secret, variable, OIDC permission, State token, archive
token, or result writer. Its single reviewed fixture restores public source
`KitaKen1/lean-eval-two-plus-two@a7cf16ee...`, benchmark
`leanprover/lean-eval@3f3786f3...`, and original evaluator
`leanprover/lean-eval-submissions@7e48191e...`. The hosted runner's image and
hardware are observed in the evidence artifact rather than pre-pinned, so this
is a staging reproducibility smoke only. It cannot consume the State replay
queue or satisfy the private/authoritative disposable-backend launch gate.

After each AWS stack exists, set only its corresponding non-secret role ARN
variable in these environments. Creating an environment shell does not enable
a workflow, grant AWS authority, or change intake.

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

The initial adapter is implemented in `scripts/aws_key_adapter.py`; its
infrastructure is `infrastructure/aws-key-adapter/template.yaml`. Deploy the
template once for staging and once for production in the dedicated account.
There is no public endpoint. Protected archive jobs use exact GitHub OIDC
subjects `archive-staging` or `archive-production` and receive only KMS Encrypt.
Protected replay controllers in `lean-eval-submissions` use
`replay-<environment>`; protected publication controllers in
`lean-eval-releases` use `release-<environment>`. Their separate roles receive
only synchronous invocation of that environment's versioned Lambda alias. Only
the Lambda role can conditionally write the environment's one-use table and
decrypt with its KMS key.

The archive GitHub environments select the **tag** pattern
`lean-eval-dispatch/*`, because server dispatch runs the reviewed workflow from
that immutable tag. `replay-staging` selects exact branch `main` for the public
replay workflow and tag pattern `lean-eval-dispatch/*` for the private replay
smoke. `replay-production` and both release environments select protected
branches only. Never configure the archive environments as “protected branches
only” or the legitimate dispatch ref will be denied; never configure any of
these six environments with unrestricted branches and tags.

The one-use table's partition key is `capability_digest`; `PutItem` uses
`attribute_not_exists(capability_digest)` before decrypt. Its
`expires_at_epoch` TTL is eventual storage cleanup, not a security decision:
the adapter rejects expired claims independently. No public API, access key,
archive-wide age identity, cross-environment role, provider-loss recovery,
backup drill, or alarm subsystem is part of this setup.

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
back at the recorded immutable commit and verifying its digest. The server
pipeline is wired to that mode: the protected `archive_state` job authenticates
the locator callback, appends the causally linked `archive.completed` event,
and updates the targeted submission view before evaluation can begin. The live
private fixture still must complete that path before staging sign-off.

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
| State-writer token provisioning | 2026-08-21 | two single-repository fine-grained tokens created, expiring 2026-11-19, installed as `GITHUB_STATE_TOKEN`, approved by `leanprover`, and verified through runs `32465890236` / `32465892118` without changing either State tree |
| State ruleset bypasses | 2026-08-21 | `kim-em` (`User` 477956) is the sole always-allowed bypass actor on staging ruleset `21094006` and production ruleset `21094005`; all protection rules otherwise unchanged |
| Browser OAuth App provisioning | 2026-08-21 | two Apps with exact per-environment callbacks; client ID and secret installed in the matching Worker |
| Broker GitHub App provisioning | 2026-08-21 | source reader `4666604` (Metadata read, Contents read, no installation) and workflow dispatcher `4666633` (Metadata read, Contents read, Actions read/write, installation `155329316` limited to `leanprover/lean-eval-submissions`); four secrets installed in both broker environments |
| Broker GitHub App ownership transfer | 2026-08-21 | both registrations accepted by `leanprover`; public records confirm unchanged IDs `4666604` / `4666633` and the exact reviewed permission split |
| Staging Source Reader preflight | 2026-08-21 | protected run `32519788255` proved the transferred App private key, staging broker binding, exact installation on `kim-em/lean-eval-intake-fixture`, and private visibility without dispatching or evaluating a submission |
| Lifecycle callback secret provisioning | 2026-08-21 | distinct random values installed in each intake Worker and its matching protected GitHub environment; values were never logged or recorded |
| Post-provisioning health check | 2026-08-21 | both Workers report `status ok`, commit `9f5db319309bfc3f4a38215fba71e4763228c2a6`, correct environment, and `intake_enabled false` |
| Automated deployment verification | 2026-08-21 | run `32437703335` created the protected immutable dispatch tag, deployed broker and intake versions for exact commit `a928be873db6569e2b4ccb3fb8b399d0f19b2e78` to staging then production, and passed both structured intake-disabled smoke checks; PRs `#1172` and `#1174` fixed the checkout-free promotion directory and payload-propagation retry discovered during the first live exercise |
| Lifecycle-overhaul terminology deployment | 2026-08-22 | run `32540475554` promoted immutable tag `lean-eval-dispatch/ee73dd0992811b1b60549fae86e59ffde4f17dc8`, deployed exact commit `ee73dd09` to staging (broker `a078c48d-6269-4d11-a0e1-80afff7dde41`, intake `f2f29886-21a7-41f3-968f-f32f913a36e7`) and production (broker `ce147759-47c7-424d-ba78-c6a01f05964f`, intake `7bf3b058-e708-473b-b9cc-a3f530209579`), and passed both structured intake-disabled smoke checks |
| Archive-verification recovery deployment | 2026-08-22 | PRs `#1213` / `#1214` changed immutable verification to decode the exact Git blob and made trusted script/recipient changes promote a fresh dispatch ref; run `32546480178` promoted `lean-eval-dispatch/1738baeb1934b28bdf44a4eb6fecaec00846ee75`, deployed staging broker/intake versions `367a191a-e779-4d2e-ba67-d21b1ecc5c4c` / `c243be75-d0a5-4e81-bd49-ab4a20106364` and production versions `ab214097-abd6-4d5d-954f-c3fedf9edcb5` / `137f4553-ea1a-468f-8dbf-ccdbc0c9129f`, and passed both structured intake-disabled smoke checks |
| Redacted public State projection | 2026-08-22 | production/staging State PRs `#4` merged as `e9477c7c` / `685f293d`; 63 tests in each repository verify strict identities, metadata, lifecycle evidence, and absence of private identifiers; read-only production deploy key `160968617` and leaderboard secret `PRODUCTION_STATE_READ_KEY` were provisioned for merged cutover PR `lean-eval-leaderboard#72` |
| Staging private-source E2E completion | 2026-08-22 | submission `01a02427-9e09-7b63-9ab7-5ff6b9ef8a09`, hosted run `32546606639`, and exact server commit `1738baeb1934b28bdf44a4eb6fecaec00846ee75` completed archive, archive-State callback, evaluation, and evaluation-State callback; the deliberate stale-proof fixture was rejected, staging State advanced to `b2160515cc18b2a871135dbe6d49df7e1bd8306d` with seven valid events and no result, and the redacted projection validated with zero results |
| Runtime-only automatic deployment trigger | 2026-08-22 | PR `#1217` excludes `server/*.md` and nested Markdown from main-branch Worker deployment while retaining runtime/config/script/audit-recipient/workflow triggers; docs-only run `32548922158` was cancelled before promotion with no Worker change; approved run `32549095770` promoted immutable tag `lean-eval-dispatch/b0a505372ddc332b5413b63e0554ee2dee690fd8`, deployed staging broker/intake `1c0cc274-4234-42c8-887a-e129a350b36e` / `ec7375aa-911e-449c-805d-5051056eb12c` and production broker/intake `56513157-722a-455f-8cb1-3b6898b9b0a2` / `e16c398f-df4b-4fb3-957c-473a2a911d8c`, and passed exact-commit intake-disabled smoke checks |
| Post-ledger live health recheck | 2026-08-22 | direct `/healthz` reads returned `status ok`, exact deployed commit `b0a505372ddc332b5413b63e0554ee2dee690fd8`, matching staging/production environment names, and `intake_enabled false` for both Workers |
| Schema-terminology deployment | 2026-08-22 | PR `#1227` merged as `70acbc1f96e38ee0838a9f1141e7e844adab07e5`; protected run `32553871300` promoted the matching immutable dispatch tag, deployed staging broker/intake versions `0c5cfbf5-1f96-4772-bc13-f44d933f8872` / `bab28e02-2e78-4dd3-9f9b-512dc2d215c0` and production broker/intake versions `36534671-b7b2-41b1-bcbb-46e6a9fce662` / `86451263-3b92-4507-a392-77b90154cdb9`, and passed exact-commit structured smoke checks with intake disabled in both environments |
| Kernel-shadow and disabled AWS-adapter deployment | 2026-08-22 | PRs `#1207` / `#1208` merged as `f47dd08c` / `a34b2053`; protected runs `32557663566` / `32557817462` passed checks, promotion, staging deploy/smoke, and production deploy/smoke. The current exact `a34b2053` versions are staging broker/intake `fa8deb74-db7c-4bb5-96ee-566397a9fdf6` / `9707b29b-b67d-49dc-93e9-d32aa530c7f5` and production broker/intake `5684fc28-b3ca-4c60-9992-e79d2f8bd576` / `3d3d9c54-4552-4a46-a1ee-6a5ec6aea5e8`; both live health responses match the commit and environment with intake disabled. No AWS account, stack, secret, variable, or authority was created. |
| Lifecycle-aware deployment | 2026-08-21 | run `32481684831` promoted immutable tag `lean-eval-dispatch/344ae1dbd5aaf53985b20511a770caa3c52b5626`, deployed that exact commit to staging and production, and passed both structured smoke checks; the State lifecycle-aware submission-view prerequisites were already merged and green |
| Staging E2E intake enable | 2026-08-21 | protected control run `32481885882` redeployed only staging intake version `ac11dee4-4bba-4328-9831-8545535d9b8f` at exact commit `344ae1dbd5aaf53985b20511a770caa3c52b5626`; health reports staging intake `true` while production remains `false` |
| AWS workload environment shells | 2026-08-22 | six empty GitHub environments verified: archive staging/production restricted to tag `lean-eval-dispatch/*`; replay staging restricted to exact branch `main` and tag `lean-eval-dispatch/*`; replay production and release staging/production restricted to protected branches; exact node, protection-rule, and tag-policy IDs are recorded above; no secret, variable, or AWS authority is present |
| Archive-before-evaluation deployment | 2026-08-21 | run `32488170650` promoted immutable tag `lean-eval-dispatch/b64a30293e82e77cc76da1f74e6f1633747e1bf0`, deployed exact commit `b64a3029` to staging (broker `edeb2d01-5acf-4099-8329-cf3e52f431e1`, intake `366c8c6d-671b-4c53-b488-e2cb86320dd3`) and production (broker `1ebdfbe1-57be-4ee4-ba80-23a9bf740fc6`, intake `3d2658ec-0fda-4bf1-9619-e7500fa61d52`), and passed both structured smoke checks; obsolete docs-only run `32482830556` at commit `5027d7dc` was cancelled without deploying so it could not block the current non-cancelling concurrency group |
| Staging intake re-enable after archive deployment | 2026-08-21 | protected control run `32488534189` verified the immutable `b64a3029` tag and deployed staging intake version `39e8392d-dcc4-46e4-9bc7-afaff28b01a5`; final health is staging `true`, production `false`, both at exact commit `b64a3029` |
| Credential-free public replay | 2026-08-21 | hosted run `32499490261` at workflow commit `757b0831018dd6ad88092eff8a2f4b3245a456d6` restored exact public source/benchmark/evaluator revisions, passed landrun and environment probes, and reproduced `two_plus_two` revision 1 through nanoda and Lean's default kernel; the downloaded three-JSON artifact independently validated with no source payload |
| Worker rollback | not run | Use only if an actual deployment needs rollback |
| AWS key-adapter staging round trip | blocked | workflow is implemented; dedicated account, stacks, and staging OIDC role variables are not provisioned |
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
