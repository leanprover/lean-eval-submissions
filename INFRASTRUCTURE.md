# lean-eval infrastructure inventory

This file is the source of truth for externally hosted lean-eval
infrastructure. A change to Cloudflare, GitHub credentials, deployment credentials,
state repositories, runner topology, DNS, or release storage is incomplete
until this ledger changes in the same pull request or an immediately linked
operations pull request. Secret **names, owners, scopes, and rotation dates**
belong here; secret values do not.

Last reconciled: 2026-08-23 (Worker commit `12da2fa5` is deployed and
intake-disabled in both environments; deployment tokens exercise Workers and
Containers; accepted-archive staging replay run `32618166048` passed;
State-writer tokens, browser OAuth Apps, and both broker GitHub Apps remain
provisioned and preflighted; the dedicated AWS key-custody account and isolated
staging and production stacks are provisioned; archive/replay staging,
historical migration, and both release role variables are connected; the
lifecycle-aware leaderboard cutover is live; D7
migrated all 44 results files and 1,298 records to schema version 2 at commit
`c3491661`; automatic release tooling is merged at `a0caa968`, with
publication still disabled and its live AWS trust update pending).
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
| GitHub release repository | `leanprover/lean-eval-releases` | production | **AUTOMATIC CONTROLLER MERGED; CREDENTIALED STAGING TRUST UPDATE PENDING; PUBLICATION DISABLED** |
| GitHub branch ruleset | `lean-eval-generator` `Protect main` (`21094079`) | shared | **ACTIVE; PR + LINEAR HISTORY + `check` REQUIRED; APPROVAL COUNT 0** |
| GitHub branch ruleset | `lean-eval-releases` `Protect main` (`21094082`) | production | **ACTIVE; PR + LINEAR HISTORY + `validate` REQUIRED; APPROVAL COUNT 0** |
| GitHub Environment | `cloudflare-staging` (`20259250422`) | staging | **CREATED 2026-08-20; ACCOUNT ID SET; API TOKEN SET 2026-08-21** |
| GitHub Environment | `cloudflare-production` (`20259250928`) | production | **CREATED 2026-08-20; ACCOUNT ID SET; API TOKEN SET 2026-08-21** |
| GitHub Environment | `submission-dispatch-promotion` (`20259251430`) | shared | **CREATED 2026-08-20; REVIEW + GUARD CONFIGURED** |
| GitHub Environment | `archive-staging` (`EN_kwDOSh7OzM8AAAAEu8r2_A`) | staging archive | **CREATED; TAG POLICY + WRAP ROLE ARN SET** |
| GitHub Environment | `archive-production` (`EN_kwDOSh7OzM8AAAAEu8r25w`) | production archive | **CREATED 2026-08-21; TAG POLICY SET; ROLE ARN NOT SET** |
| GitHub Environment | `replay-staging` (`EN_kwDOSh7OzM8AAAAEu8r21Q`) | staging replay | **CREATED; MAIN + DISPATCH TAG POLICIES + INVOKER ROLE ARN SET** |
| GitHub Environment | `replay-production` (`EN_kwDOSh7OzM8AAAAEu8r3MQ`) | production replay | **CREATED 2026-08-21; PROTECTED BRANCHES ONLY; ROLE ARN NOT SET** |
| GitHub Environment | `archive-migration-production` (`EN_kwDOSh7OzM8AAAAEwLDSMQ`) | historical migration | **CREATED 2026-08-23; READ KEY + PRODUCTION WRAP ROLE SET; LEGACY IDENTITY ABSENT** |
| GitHub Environment | `release-staging` (`EN_kwDOT-oWes8AAAAEu8r3Mw`) | staging release | **ROLE + READ KEYS SET; LIVE OIDC TRUST UPDATE PENDING** |
| GitHub Environment | `release-production` (`EN_kwDOT-oWes8AAAAEu8r3KQ`) | production release | **ROLE + CONTROLLER/PUBLISH KEYS SET; PUBLICATION VARIABLE ABSENT** |
| AWS account | `lean-eval` (`161072922960`) | dedicated key custody | **CREATED; ROOT MFA ENABLED; NO ACCESS KEYS** |
| AWS CloudFormation stack | `lean-eval-key-adapter-staging` | staging | **PROVISIONED; RELEASE OIDC TRUST UPDATE PENDING** |
| AWS CloudFormation stack | `lean-eval-key-adapter-production` | production | **PROVISIONED; RELEASE OIDC TRUST UPDATE PENDING; INTAKE/REPLAY/PUBLICATION DISABLED** |
| Cloudflare replay Worker | `lean-eval-replay-executor-staging` | staging | **CORRECTED IMAGE ACCEPTANCE PASSED 2026-08-23; REPLAY DISABLED; STATE RECONFIGURATION PENDING** |
| Cloudflare replay Worker | `lean-eval-replay-executor` | production | **PROVISIONED 2026-08-22; REPLAY AND ACCEPTANCE DISABLED** |
| Replay execution backend | Cloudflare Sandbox, provider-neutral adapter | staging / production | **CORRECTED IMAGE AND RUNTIME PROFILE FROZEN; STAGING/PRODUCTION REPLAY DISABLED PENDING STATE RECONFIGURATION** |

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
| Billing plan / cost owner | Workers Paid activated 2026-08-22 / Kim Morrison (temporary) |
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

Replay Worker and container configuration is separately declarative in
[`server/wrangler.replay.jsonc`](server/wrangler.replay.jsonc). The reviewed
container is built only from
[`Dockerfile.replay-authoritative`](Dockerfile.replay-authoritative) by the
protected publication workflow, then referenced by its fully qualified
Cloudflare Registry tag and independently recorded manifest digest:

- Cloudflare Sandbox SDK `0.12.7`, backed by base image
  `cloudflare/sandbox:0.12.7@sha256:6d741713aef266e8ae0831a5709c6f2d7b77b4952ac79b549f4f4e380af86fbe`;
- one `standard-4` container at most per environment (4 vCPU, 12 GiB RAM,
  20 GB disk), with SSH disabled and public network access disabled in the
  Sandbox class;
- staging exposes the synthetic and accepted-archive acceptance endpoints;
  general replay is disabled in staging and both acceptance and replay are
  disabled in production;
- the 2026-08-23 failed authoritative rollout was followed by disabled-state
  acceptance probes; live Cloudflare logs showed exit code 127 because the
  authoritative image omitted the two acceptance commands. The Dockerfile and
  publication gate now require all four fixed commands. The corrected immutable
  image passed the accepted-archive boundary, and deployment now waits for the
  exact container image to report healthy before the authoritative retry;
- every request gets a fresh nonce-derived sandbox ID, no default persistent
  session, a fixed command, bounded inputs, and unconditional `destroy()`;
- one 12 GiB `standard-4` is the approved staging and production ceiling;
  resource-limit outcomes do not authorize retry on an unreviewed larger
  profile;
- the stable request/evidence contract contains no Cloudflare identifier, so a
  later execution-provider migration does not alter State, archive, capability,
  or verdict formats.

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
`12da2fa504ea4b9408d9fb24773886df02e20d66`:

| Environment | Private broker version | Replay Worker version / container application / digest | Intake Worker version | Health verification |
| --- | --- | --- | --- | --- |
| staging | `d4c25df8-c51e-436f-9bf8-1026521f111e` | `ea923507-01cc-4d05-bcc2-19a00e37bc47` / `a0361b52-feb3-4ee7-a68e-1ce63d78f2ec` / `sha256:a067a3ded4ea70c9063e78ebb4c6c8da2cfa7d11c5883d550f86dbf08d639b76` | `8fd16847-e491-44f8-9b3e-137b6512cb95` | environment `staging`, intake `false`, replay `false`, acceptance `true`, both memory fields `12884901888`, exact commit |
| production | `dd5d24df-9cb7-40ec-9958-0b66454bbf94` | `a22bfd7f-a162-4bbe-8ee7-eafe897dcad6` / `a03ed768-89ad-4a0d-ad8d-cccdabc7775d` / `sha256:bc4a4c5405cef246eb29666f9ed8baf37d65377cfa65cc72cc5870c50cd386fa` | `b689fed9-444f-440e-923e-3f829162e307` | environment `production`, intake `false`, replay `false`, acceptance `false`, both memory fields `12884901888`, exact commit |

This manual bootstrap does not replace deployment automation.
`CLOUDFLARE_ACCOUNT_ID` and a distinct, narrowly scoped
`CLOUDFLARE_API_TOKEN` are installed in each Cloudflare environment. The
2026-08-21 post-merge deployment verified both opaque tokens successfully.
Cloudflare Sandbox was selected for the disposable replay executor on
2026-08-22. The operator added Containers: Edit to both existing deployment
tokens on 2026-08-22. Protected run `32573880099` used each opaque token to
publish and roll out its exact environment-specific image, proving the added
permission without revealing either token.

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
2. staging broker, replay Worker/container, and intake Worker deploys, followed
   by exact structured `GET /healthz` smoke tests;
3. production broker, disabled replay Worker/container, and intake Worker
   deploys, followed by the same exact health gates.

GitHub environment `cloudflare-staging` must contain:

| Name | Kind | Required scope |
| --- | --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | secret | Cloudflare account identifier |
| `CLOUDFLARE_API_TOKEN` | secret | Workers Scripts edit plus Containers edit for the dedicated Lean Eval account; no zone or DNS permission |

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
| `lean-eval-deploy-staging` | `cloudflare-staging` | Workers Scripts: Edit; Containers: Edit, live-verified in run `32573880099`; entire `lean-eval` account | 2026-08-21 | none |
| `lean-eval-deploy-production` | `cloudflare-production` | Workers Scripts: Edit; Containers: Edit, live-verified on a disabled container in run `32573880099`; entire `lean-eval` account | 2026-08-21 | none |

Neither token may carry any other permission. Each was checked at creation
against `/accounts/<id>/tokens/verify` (active), `/accounts/<id>/workers/scripts`
(the four Lean Eval Workers, and nothing else), `/zones` (zero zones), and
`/accounts/<id>/storage/kv/namespaces` (denied). They carry no expiry so an
unattended deployment cannot fail on a lapsed credential; rotation is therefore
manual and owned by Kim Morrison. See
Cloudflare's [GitHub Actions authentication
guide](https://developers.cloudflare.com/workers/ci-cd/external-cicd/github-actions/)
and [permission reference](https://developers.cloudflare.com/fundamentals/api/reference/permissions/).

Permission-expansion procedure: select account `lean-eval`, open **Manage
Account > API Tokens**, edit each named token, retain Account / Workers Scripts
/ Edit, add only Account / Containers / Edit, retain the one-account resource
scope, and leave zone resources, IP filtering, expiry, and all other
permissions unchanged. If Cloudflare replaces rather than edits a token, put
the new opaque value only into its matching GitHub environment, verify a
container deployment, then revoke the old value. No token value belongs in
this ledger.

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

The selected root-key platform is AWS KMS in the dedicated `lean-eval` AWS
account. AWS holds archive identities and the small one-use unwrap gate only;
it does not run Lean evaluation or replay compute.

| Field | Recorded value |
| --- | --- |
| AWS account purpose | Lean Eval archive-envelope root and audit only |
| AWS account name / ID | `lean-eval` / `161072922960` |
| Root/contact email | `kim+lean-eval@lean-fro.org`; never a workload credential |
| Billing owner | Kim Morrison (temporary) |
| Primary administrator | Kim Morrison; temporary root console sessions only, with MFA enabled and no root access key |
| AWS Organizations / IAM Identity Center | not enabled in this standalone account; a future Lean FRO organization may invite the account and supply centralized administration |
| Provider-loss recovery | None by design; planned migration requires the active provider |
| KMS region | `us-east-1` selected for the initial small service; the stable contract is region-neutral |
| GitHub OIDC provider | `arn:aws:iam::161072922960:oidc-provider/token.actions.githubusercontent.com`; sole audience `sts.amazonaws.com`; thumbprint `ab9d0263244dd0326eb67015705a667e79cfe998` |
| Staging stack | `lean-eval-key-adapter-staging`; `CREATE_COMPLETE`; stack ID `2251e410-9e15-11f1-a8ef-0eba172391bd` |
| Production stack | `lean-eval-key-adapter-production`; `CREATE_COMPLETE`; stack ID `6ab5d7c0-9e15-11f1-9a35-0affda52f513` |
| SAM artifact stack / bucket | `aws-sam-cli-managed-default` / `aws-sam-cli-managed-default-samclisourcebucket-ygefen7ybulh`; bucket blocks all public access, uses KMS server-side encryption, and has versioning enabled |
| Account-default resources | AWS-managed service-linked roles for Resource Explorer, Support, and Trusted Advisor; one local Resource Explorer index in `ap-southeast-2` with no view; none grants Lean Eval workload authority |

Recorded stack outputs:

| Output | Staging | Production |
| --- | --- | --- |
| Adapter | `aws-kms-v1` | `aws-kms-v1` |
| KMS key ARN | `arn:aws:kms:us-east-1:161072922960:key/7e15960c-7de0-43ac-bb42-e31683cbea9f` | `arn:aws:kms:us-east-1:161072922960:key/219904f9-4952-400f-b60a-6f027c4d070b` |
| KMS alias | `alias/lean-eval-archive-identities-staging` | `alias/lean-eval-archive-identities-production` |
| One-use table | `lean-eval-capability-consumption-staging` | `lean-eval-capability-consumption-production` |
| Versioned unwrap alias ARN | `arn:aws:lambda:us-east-1:161072922960:function:lean-eval-archive-unwrap-staging:live` | `arn:aws:lambda:us-east-1:161072922960:function:lean-eval-archive-unwrap-production:live` |
| Archive Wrap role ARN | `arn:aws:iam::161072922960:role/lean-eval-archive-wrap-staging` | `arn:aws:iam::161072922960:role/lean-eval-archive-wrap-production` |
| Replay invoker role ARN | `arn:aws:iam::161072922960:role/lean-eval-replay-unwrap-invoker-staging` | `arn:aws:iam::161072922960:role/lean-eval-replay-unwrap-invoker-production` |
| Release invoker role ARN | `arn:aws:iam::161072922960:role/lean-eval-release-unwrap-invoker-staging` | `arn:aws:iam::161072922960:role/lean-eval-release-unwrap-invoker-production` |
| Function role | `lean-eval-archive-unwrap-function-staging` | `lean-eval-archive-unwrap-function-production` |

GitHub's repository OIDC API currently reports subject prefixes
`repo:leanprover/lean-eval-submissions` and
`repo:leanprover@7233018/lean-eval-releases@1340741242`. The releases repository
was transferred after GitHub's immutable-subject rollout, so its owner and
repository IDs are part of every token subject even though its display name is
unchanged. The stack template pins these API-reported prefixes separately.
Credentialed release staging run `32617539355` proved the previous live role
still trusted the obsolete name-only release subject: it failed at STS before
Lambda invocation, consumed no capability, and decrypted no source. Apply the
reviewed stack update before repeating that smoke; do not opt the repository
out of immutable subjects to preserve an obsolete trust policy.

Both keys are enabled customer-managed symmetric keys with annual rotation.
Both one-use tables are active, on-demand, server-side encrypted, and use
`expires_at_epoch` TTL. Both Lambda `live` aliases point to immutable version
`1`; the functions are Python 3.13, 128 MiB, ten-second timeout, and have no
Function URL or API Gateway. The account's minimum Lambda concurrency quota
does not permit a reserved concurrency of one while retaining AWS's required
ten unreserved executions. The function therefore has no reserved concurrency;
the atomic conditional DynamoDB insert, not Lambda serialization, enforces
one-use correctness.
The first live staging invocation automatically created CloudWatch log group
`/aws/lambda/lean-eval-archive-unwrap-staging` with AWS's default indefinite
retention; its only application error is the expected synthetic second-use
rejection and contains no source or identity. Production has not been invoked
and has no corresponding log group.

The GitHub environment shells were created before the AWS account so their ref
boundaries could be verified without granting AWS authority. Archive and replay
staging, the guarded historical migration, and both release environments now
contain the non-secret role variables shown below. Production archive and
production replay remain unconnected; release publication remains disabled:

| Repository | Environment | Environment node | Protection rule | Ref policy | Policy ID |
| --- | --- | --- | --- | --- | --- |
| `leanprover/lean-eval-submissions` | `archive-staging` | `EN_kwDOSh7OzM8AAAAEu8r2_A` | `63321649` | tag `lean-eval-dispatch/*` | `57914845` |
| `leanprover/lean-eval-submissions` | `archive-production` | `EN_kwDOSh7OzM8AAAAEu8r25w` | `63321647` | tag `lean-eval-dispatch/*` | `57914846` |
| `leanprover/lean-eval-submissions` | `replay-staging` | `EN_kwDOSh7OzM8AAAAEu8r21Q` | `63352004` | branch `main`; tag `lean-eval-dispatch/*` | `57941304`; `57941307` |
| `leanprover/lean-eval-submissions` | `replay-production` | `EN_kwDOSh7OzM8AAAAEu8r3MQ` | `63321654` | protected branches only | not applicable |
| `leanprover/lean-eval-submissions` | `archive-migration-production` | `EN_kwDOSh7OzM8AAAAEwLDSMQ` | `63434355` | protected branches only | not applicable |
| `leanprover/lean-eval-releases` | `release-staging` | `EN_kwDOT-oWes8AAAAEu8r3Mw` | `63321653` | protected branches only | not applicable |
| `leanprover/lean-eval-releases` | `release-production` | `EN_kwDOT-oWes8AAAAEu8r3KQ` | `63321651` | protected branches only | not applicable |

| Repository / environment | Variable | Value |
| --- | --- | --- |
| `leanprover/lean-eval-submissions` / `archive-staging` | `AWS_WRAP_ROLE_ARN` | `arn:aws:iam::161072922960:role/lean-eval-archive-wrap-staging` |
| `leanprover/lean-eval-submissions` / `replay-staging` | `AWS_REPLAY_UNWRAP_ROLE_ARN` | `arn:aws:iam::161072922960:role/lean-eval-replay-unwrap-invoker-staging` |
| `leanprover/lean-eval-submissions` / `archive-migration-production` | `AWS_WRAP_ROLE_ARN` | `arn:aws:iam::161072922960:role/lean-eval-archive-wrap-production` |
| `leanprover/lean-eval-releases` / `release-staging` | `AWS_RELEASE_UNWRAP_ROLE_ARN` | `arn:aws:iam::161072922960:role/lean-eval-release-unwrap-invoker-staging` |
| `leanprover/lean-eval-releases` / `release-production` | `AWS_RELEASE_UNWRAP_ROLE_ARN` | `arn:aws:iam::161072922960:role/lean-eval-release-unwrap-invoker-production` |

Read/write deploy-key inventory: release staging uses audit key `161041215`
and State key `161041214`; release production uses audit key `161041000`,
write-capable State controller key `161040898`, and release publisher key
`161040897`. The production secrets are installed, but no
publication-enabling variable exists.
Historical migration uses read-only audit key `161041934`; its required
`LEGACY_ARCHIVE_IDENTITY` secret is deliberately absent. Accepted-archive
replay uses distinct read-only State key `161043118` and audit key `161043119`.
The staging authoritative replay controller additionally owns write-capable
State key `161051584`; its private half exists only as
`STAGING_STATE_WRITE_KEY` in protected environment `replay-staging`. Staging
State ruleset `21094006` allows deploy-key bypass so this key can publish one
locally validated non-force append. The two existing read-only deploy keys
cannot use that bypass to write, and no production replay writer was created.

The manual `public-replay-smoke.yml` job uses `replay-staging` but needs and
receives no environment secret, variable, OIDC permission, State token, archive
token, or result writer. Its single reviewed fixture restores public source
`KitaKen1/lean-eval-two-plus-two@a7cf16ee...`, benchmark
`leanprover/lean-eval@3f3786f3...`, and original evaluator
`leanprover/lean-eval-submissions@7e48191e...`. The hosted runner's image and
hardware are observed in the evidence artifact rather than pre-pinned, so this
is a staging reproducibility smoke only. It cannot consume the State replay
queue or satisfy the private/authoritative disposable-backend launch gate.

Do not set the production archive/replay variables. The installed release role
variables and keys do not enable publication: the controller still requires
its separate publication variable, which is absent. Staging variables do not
enable intake or authoritative queue consumption.

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
a persistent workspace. The selected initial backend is the dedicated
Cloudflare Sandbox executor recorded above. It remains replaceable without
changing State, archive, request, or verdict contracts; no existing project
runner is part of the boundary.

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
| State ruleset bypasses | 2026-08-23 | Production ruleset `21094005` retains `kim-em` (`User` 477956) as its sole always-allowed bypass actor. Staging ruleset `21094006` retains that user and now also permits `DeployKey` bypass for authoritative replay writer key `161051584`; deletion, non-fast-forward, linear-history, pull-request, and exact `validate` status rules remain active. Existing staging deploy keys `161041214` / `161043118` are read-only. |
| Staging replay State writer | 2026-08-23 | Write-capable deploy key `161051584` is scoped only to `leanprover/lean-eval-state-staging`; its private half was installed only as protected `replay-staging` secret `STAGING_STATE_WRITE_KEY` and securely removed from local scratch. Production State, replay, intake, and release configuration were unchanged. |
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
| Lifecycle-aware leaderboard cutover | 2026-08-22 | `lean-eval-leaderboard#72` merged as `dd5d508d`; Pages run `32557778003` regenerated and validated the redacted production projection, built the full site, checked the lifecycle-aware cutover and internal links, and deployed successfully. Direct public checks returned 200 for `/eval/`, `/eval/legacy/`, `/eval/preview/`, lifecycle assets and schema-version-2 site data, plus representative stable problem route `/eval/problems/annals_absolute_profinite_rigidity/`; the stable root has the lifecycle application marker and no preview badge, and the published v1 scope contains 128 members. |
| Autonomous generator/release merge policy | 2026-08-22 | At maintainer direction, generator ruleset `21094079` and release ruleset `21094082` changed only `required_approving_review_count` from 1 to 0. Pull requests, exact required GitHub Actions checks (`check` / `validate`), strict up-to-date status, linear history, review-thread resolution, deletion protection, and non-fast-forward protection remain active; no bypass actor was added. Generator PRs `#1` / `#2` / `#3` merged to green main `77373a53`; release PRs `#3` / `#1` / `#2` merged to green publication-disabled main `f1f83344`. |
| Standalone generator consumer merge | 2026-08-22 | `lean-eval#553` merged as `b91d4757aa0d7776c02540c9089df54fa0d0658a`, removing the embedded generator core and pinning standalone generator main `77373a539b31f8f304c852f288d7d8469cceebff`. Pull-request run `32559642804` passed repository and security/scoring checks, all eight catalog shards, the aggregate inventory check, and final verification. |
| Lifecycle-aware deployment | 2026-08-21 | run `32481684831` promoted immutable tag `lean-eval-dispatch/344ae1dbd5aaf53985b20511a770caa3c52b5626`, deployed that exact commit to staging and production, and passed both structured smoke checks; the State lifecycle-aware submission-view prerequisites were already merged and green |
| Staging E2E intake enable | 2026-08-21 | protected control run `32481885882` redeployed only staging intake version `ac11dee4-4bba-4328-9831-8545535d9b8f` at exact commit `344ae1dbd5aaf53985b20511a770caa3c52b5626`; health reports staging intake `true` while production remains `false` |
| AWS workload environment shells | 2026-08-22 | six GitHub environments verified: archive staging/production restricted to tag `lean-eval-dispatch/*`; replay staging restricted to exact branch `main` and tag `lean-eval-dispatch/*`; replay production and release staging/production restricted to protected branches. Only `archive-staging` and `replay-staging` contain their exact non-secret role ARN variable; the other four remain empty and unconnected. |
| AWS key-custody provisioning | 2026-08-22 | dedicated account `lean-eval` (`161072922960`) verified with root MFA, no access keys, no Organization, and no Identity Center. GitHub OIDC provider and isolated staging/production stacks reached `CREATE_COMPLETE`; KMS rotation, exact repository/environment trust, Encrypt-only and alias-Invoke-only policies, DynamoDB conditional-write boundary/TTL/SSE, immutable Lambda aliases, and absence of public Lambda URLs were inspected. SAM bucket `aws-sam-cli-managed-default-samclisourcebucket-ygefen7ybulh` is private, KMS-encrypted, and versioned. An initial staging rollback caused by the new account's minimum Lambda concurrency quota scheduled unused key `292b5069-d782-474b-afbb-071d7be281f3` for deletion on 2026-09-21; no alias or usable authority remains. The unnecessary reserved-concurrency setting was removed because the atomic DynamoDB insert enforces one-use. Production role variables remain unset. |
| AWS provisioning merge deployment | 2026-08-22 | PR `#1239` merged as `d487c9d5`; protected run `32568464179` promoted immutable tag `lean-eval-dispatch/d487c9d5b1a22a7a7dd27d729f3eb642c6474b1a`, deployed staging broker/intake versions `74bc2395-cbab-4dc5-b889-a0d7d52ccdf1` / `ec858ec9-336e-4a9b-9c9b-21e5f345c37b` and production versions `7c5edcca-2d9f-44aa-b02b-eae603968ab3` / `a38255d0-dc57-4b4c-94cb-47ee7f1a0f21`, and passed both exact-commit intake-disabled health checks. |
| Archive-before-evaluation deployment | 2026-08-21 | run `32488170650` promoted immutable tag `lean-eval-dispatch/b64a30293e82e77cc76da1f74e6f1633747e1bf0`, deployed exact commit `b64a3029` to staging (broker `edeb2d01-5acf-4099-8329-cf3e52f431e1`, intake `366c8c6d-671b-4c53-b488-e2cb86320dd3`) and production (broker `1ebdfbe1-57be-4ee4-ba80-23a9bf740fc6`, intake `3d2658ec-0fda-4bf1-9619-e7500fa61d52`), and passed both structured smoke checks; obsolete docs-only run `32482830556` at commit `5027d7dc` was cancelled without deploying so it could not block the current non-cancelling concurrency group |
| Staging intake re-enable after archive deployment | 2026-08-21 | protected control run `32488534189` verified the immutable `b64a3029` tag and deployed staging intake version `39e8392d-dcc4-46e4-9bc7-afaff28b01a5`; final health is staging `true`, production `false`, both at exact commit `b64a3029` |
| Credential-free public replay | 2026-08-21 | hosted run `32499490261` at workflow commit `757b0831018dd6ad88092eff8a2f4b3245a456d6` restored exact public source/benchmark/evaluator revisions, passed landrun and environment probes, and reproduced `two_plus_two` revision 1 through nanoda and Lean's default kernel; the downloaded three-JSON artifact independently validated with no source payload |
| Worker rollback | not run | Use only if an actual deployment needs rollback |
| AWS key-adapter staging round trip | 2026-08-22 | authoritative run `32568604230` at immutable tag/commit `d487c9d5b1a22a7a7dd27d729f3eb642c6474b1a` passed gate, Encrypt-only OIDC assumption and wrap, source-free ciphertext handoff, Invoke-only assumption, first consume/decrypt, identical second-use rejection, AWS-authority removal, and local synthetic-source decryption. Staging contains one synthetic TTL item; production contains zero. Initial run `32568171403` had stopped before unwrap on the mistyped action pin corrected by `#1239`. No State event, result, release, production AWS variable, or replay backend was created. |
| Cloudflare replay deployment and synthetic acceptance | 2026-08-22 | PR `#1242` merged the provider-neutral executor as `75d1f7a6`; PR `#1243` added the image's explicit Python runtime as `160bd6e3`. Protected deployment run `32573880099` published exact staging/production images through the two expanded deployment tokens, deployed the version and application IDs recorded above, and passed exact-commit health with intake and replay disabled. Acceptance run `32574078784` at immutable tag `lean-eval-dispatch/160bd6e395495eeb5ff94c6f6bc3e714f53d7560` passed wrong-archive refusal before consumption, one successful unwrap, identical reuse refusal, AWS-authority removal, exact ciphertext/marker verification inside a fresh 12 GiB Sandbox, blocked public egress, source-free evidence, and confirmed destruction. Live tail independently recorded fixed-command exit `0`, 20.414-second execution, `destroy` success in 272 ms, and zero mounts. Diagnostic runs `32573615982` / `32573716928` exposed the missing image runtime; both still confirmed Sandbox destruction and wrote no State event, result, release, or production authority. After verifying the two active digests, the broken unreferenced diagnostic tags `5a304d0d` (`sha256:8d714e45…`) and `b8e41176` (`sha256:37bd9f5c…`) were deleted; registry inventory now contains only active tags `c97d7986` and `2459f8fa`, both reproducible from protected source. |
| Schema-version-3 archive and accepted-result staging | 2026-08-23 | PRs `#1250` / `#1251` replaced the server archive path with one fresh KMS envelope per submission and completed the accepted result/State callback. Submission `01a02c83-79f7-730b-9bcd-8cdac4fa5d7a` archived at audit commit `3ac5dfdcd9f8fde336775f194fe4e9fad1a182bc` and produced accepted result `r2_99df81809318fd2673d82da042b451f77b55606c6b506beb4526828ee1e7079e`; submission `01a02cb4-5e7c-7fb3-a4ab-b6fabbb72584` completed run `32615934053`, archived at `92b95c162ad9bf38d027e11193683ca61ed2a994`, and wrote its accepted result and State callback. Production intake remained disabled. |
| Historical archive migration dry run | 2026-08-23 | PR `#1252` merged guarded, source-free migration tooling. Credentialed dry run `32616816083` at audit commit `92b95c162ad9bf38d027e11193683ca61ed2a994` validated exactly 1,040 migrations and two retained schema-version-3 objects with canonical digest `48f55807f430d8754e4a7b79cb391d582028df6abce347d037bd810a0e3decfa`; all decrypt, wrap, and write operations were skipped. Apply remains blocked on the custodian-supplied `LEGACY_ARCHIVE_IDENTITY`. |
| Accepted staging archive replay boundary | 2026-08-23 | PRs `#1253`–`#1256` added exact private execution planning, aligned the public schema, introduced the separately credentialed accepted-archive route, pinned the 12 GiB production ceiling, and corrected immutable release OIDC trust in the template. Deployment run `32617911271` promoted and deployed exact commit `12da2fa504ea4b9408d9fb24773886df02e20d66` to both environments and passed all intake-/replay-disabled health checks. Immutable-tag run `32618166048` selected accepted submission `01a02cb4-5e7c-7fb3-a4ab-b6fabbb72584`, bound audit commit `92b95c162ad9bf38d027e11193683ca61ed2a994`, consumed one exact replay capability, proved identical reuse refusal and AWS-authority removal, verified plaintext digest/size and safe tar shape only in a fresh network-disabled Sandbox, and confirmed destruction. It uploaded and wrote nothing. Earlier run `32618094637` deliberately failed closed with HTTP 401 because it was mistakenly dispatched from mutable `main`; it consumed one capability before the independent Worker OIDC check, so the workflow now rejects mutable refs before State reads or AWS assumption. Authoritative checker execution, State queue consumption, general replay, and production replay remain disabled. |
| Authoritative replay image publication | 2026-08-23 | Protected publication run `32631769276` built source commit `fdcabb95085edccd70c81dc079c27bcaf20a4b16` once, passed the isolated image tests and 20 GiB size ceiling, pushed tag `lean-eval-authoritative:fdcabb95085edccd70c81dc079c27bcaf20a4b16`, and resolved immutable manifest `sha256:53d1964edc01f736ae66d7faa715d5b1fb67c96dcc167b4c5012282d8c14c807`. Its evidence records image size `11851110946`, benchmark commit `b91d4757aa0d7776c02540c9089df54fa0d0658a`, Dockerfile digest `b3cea50b400fed4da1d37b277129feac09682caff43995ddd826c19cf2ba4e72`, and profile-lock digest `548b899107a895124083a0018b1204c0079e2f8b5f44d02f57056f30a50f038d`. The deploy workflow verifies this exact registry manifest before either environment can mutate, and replay remains disabled. |
| Corrected authoritative replay image publication | 2026-08-23 | PR `#1274` merged the packaging repair as `48525d13562f99fc8f24d8467ec3855005474195` after exact-image run `32637888750` built the image and verified all four fixed commands, including both acceptance executables. Protected deployment run `32640722206` then passed exact health in staging and production with intake/replay disabled. Publication run `32640872950` built the protected main commit once, enforced the same command and 20 GiB gates, pushed immutable tag `lean-eval-authoritative:48525d13562f99fc8f24d8467ec3855005474195`, and resolved manifest `sha256:dd790c0c84eabac20c48e827a825809ea5a35e3baefd03c40609f9fdca80f6fc`. Source-free evidence records image size `11851131640`, Dockerfile digest `a258cf9b7bfd0d3b82db5b5ea0c263dc722ca331500efe21d08b2485e201ccb1`, benchmark commit `b91d4757aa0d7776c02540c9089df54fa0d0658a`, and unchanged profile-lock digest `548b899107a895124083a0018b1204c0079e2f8b5f44d02f57056f30a50f038d`. The corrected image must still be deployed with zero review digests, pass the accepted-archive runtime boundary, and be refrozen before any retry. |
| Authoritative replay runtime and configuration freeze | 2026-08-23 | PR `#1269` merged as `801f5a8dee32163bb0fa7615c833164b0fc646ae`; protected deployment run `32634597269` independently resolved the reviewed manifest before each environment mutation, deployed it to staging and production, and passed exact health with intake/replay disabled. Accepted-archive runtime run `32634743290` then bound that immutable deployment to submission `01a02cb4-5e7c-7fb3-a4ab-b6fabbb72584`, one-use unwrap and reuse rejection, 12 GiB `standard-4`, x86_64 AMD EPYC, kernel `6.18.36-cloudflare-firecracker-2026.6.17`, blocked egress, and confirmed destruction without writing State or a result. The source-free publication/runtime evidence froze [`configuration/authoritative-replay-staging-v1.json`](configuration/authoritative-replay-staging-v1.json): execution profile `8c106b325a1e5bd3a2dce9f4941a8d6c6768c1bdbd749a058d18fc99dcf7544a`, measurement config `2dfc898270b83b6c99689e3f551a102c5e76636ec9f469a408498080e3e45945`, and VM manifest `sha256:53d1964edc01f736ae66d7faa715d5b1fb67c96dcc167b4c5012282d8c14c807`. General replay remains disabled. |
| Corrected replay runtime and configuration freeze | 2026-08-23 | PR `#1275` merged as `5b4a3deccceaff371bb528557c7efb836b4291bd`; deployment run `32643920381` pinned manifest `sha256:dd790c0c84eabac20c48e827a825809ea5a35e3baefd03c40609f9fdca80f6fc` with replay disabled and both review digests zero. Runs `32644087136` / `32644223485` then failed closed at the acceptance command because Worker health converged before Cloudflare's asynchronous container rollout: live logs showed the old image exiting 127 and confirmed sandbox destruction, while the application did not report corrected image version 19 ready until `2026-08-23T14:04:50Z`. Run `32644354403`, dispatched only after that exact ready state, passed one-use unwrap/reuse refusal, decrypt, safe archive shape, blocked egress, x86_64 AMD EPYC / kernel `6.18.36-cloudflare-firecracker-2026.6.17`, and destruction. No run wrote State or a result. Source-free publication/runtime evidence now freezes execution profile `05b2602c71cd9ee168a11d0e79aa47c9c4ebfbd642b7a23ec988c8906d4f3483`, unchanged measurement config `2dfc898270b83b6c99689e3f551a102c5e76636ec9f469a408498080e3e45945`, and configuration file SHA-256 `a82a036ca928fa6318961531dc0a121aebbf051b9cc2d32c8efd28fd71f9d04b`. PR `#1276` merged the frozen pins and authoritative container-info rollout gate as `a72fc915b8bf4b252341f6348c1528774aaac328`; protected run `32645810895` passed exact staging and production rollout readiness plus health with intake/replay disabled, and CI run `32645810838` passed. Staging State PR `#10` then appended the reviewed `replay.reconfigured` event and merged as `4534ce8217459203af0868a9724a1d744c7e5b5e`; post-merge validator run `32646117007` passed. The staging queue now preserves retryable attempt 1 while binding the corrected profile and exact configuration evidence. Production replay and both intake paths remain disabled. |
| Corrected authoritative replay retry | 2026-08-23 | PR `#1277` enabled only staging general replay as `cfbe3be1963605d62f7a291a289df19cebc899bb`; protected run `32646378464` passed exact container readiness and health with staging replay enabled, production replay disabled, and intake disabled in both environments. Controller run `32646544734` consumed the reconfigured queue item as attempt 2. It durably appended `replay.started` at staging State commit `2935e995248e85be74e411a5b8b55d0419fb3d31`, verified the exact enabled health/profile/image, fetched the immutable audit object, consumed one AWS unwrap capability, removed AWS authority, and then received HTTP 500 from the authoritative executor after 24 seconds. It failed closed, scrubbed private scratch, and appended retryable `runner_lost` at State commit `a43a43e3392d8fd190b5c1aec8cc66ef11d2d8fb`. No verdict, result, release, or production authority was written. Staging general replay was immediately returned to disabled configuration pending sanitized Worker-log diagnosis. |
| Authoritative replay failure isolation | 2026-08-23 | Accepted-archive run `32647102807` at immutable tag `lean-eval-dispatch/1324064b5f1eca64843252269d31d1e8989764b2` passed exact disabled health, State selection, one-use AWS unwrap and reuse refusal, AWS-authority removal, decryption, safe archive validation, blocked egress, and confirmed destruction for the same accepted archive used by attempt 2. Its source-free artifact `authoritative-replay-runtime-evidence` (`9495167555`) has SHA-256 `c13e7e6364db1d863fc3c351ba7d11b94e113810eb9603d0ee0d6dcdf599ac83` and exactly matches the frozen x86_64 AMD EPYC / `6.18.36-cloudflare-firecracker-2026.6.17` runtime profile. Independent source-local validation found exactly one locked workspace. Together these checks isolate the attempt-2 HTTP 500 to the later evaluator, measurement-evidence, or verdict-validation path; they wrote no State event, result, release, or production authority. PR `#1279` merged the authenticated closed-vocabulary diagnostic path as `457b24aade6e43abfa3fc45552bd8798430a06ea`; CI `32647866107` and protected deployment `32647866100` passed exact disabled staging and production health. PR `#1280` enables only one sanitized staging diagnostic retry; production replay and both intake paths remain disabled. |
| Sanitized authoritative replay attempt 3 | 2026-08-23 | PR `#1280` merged as `e514e9d7e27f19f719be5141470dda2f03aa300e`; CI `32648225127` and protected deployment `32648225128` passed exact staging replay-enabled and production replay-disabled health with both intake paths disabled. The initial run `32648394569` was a complete no-op because its required boolean confirmation remained false. Confirmed controller run `32648414599` durably appended attempt-3 `replay.started` event `01a02f38-c9f0-7752-93ca-bdafc5617241` at State commit `20764074654ea933c8dd60dfa91cd264ca59ec48`, consumed and rejected reuse of one exact AWS unwrap capability, removed AWS authority, and then received the authenticated sanitized classification `command_failed` / `evaluator_unavailable`. Real-time Cloudflare tail independently recorded image version `12ad40d4-5591-43cd-a76e-d278e15f0e7a`, fixed-command exit 1 after 20.827 seconds, HTTP 500, and successful destruction in 273 ms with zero mounts. The controller appended retryable `runner_lost` event `01a02f39-db60-7931-91ec-269acdf4e259`; State main is `f3f25adfd3e761d5c2aa8bbab8a8cfc44f6b20d4`. No verdict, result, release, or production authority was written. PR `#1281` returns staging replay to disabled configuration before correcting the authoritative image's evaluator import. |
| Python 3.11 authoritative image publication and freeze | 2026-08-23 | PR `#1282` merged the explicit Python 3.11 runtime and evaluator-import gates as `f1e398e6ce15eea09a5b9c5289c2cbd56003aef4`. Pull-request image run `32648980667` built the exact image, verified Python 3.11, `tomllib`, and `detect_matches`, and recorded source-free evidence SHA-256 `ab484f55cee1150a00d9dcf2402a1977e971b2adcb875ef24bf5ce02536f66bb`; protected CI `32651426556` and disabled deployment `32651426564` then passed. Protected publication run `32651483749` rebuilt that exact reviewed commit once, repeated the content/import and 20 GiB gates, pushed immutable tag `lean-eval-authoritative:f1e398e6ce15eea09a5b9c5289c2cbd56003aef4`, and resolved manifest `sha256:f09248ec7cf33887acfaf56430bd9e410189ce63e1f1b85b94804498de4e9ef2`. Publication evidence artifact `9497014657` has file SHA-256 `7eab294a8cc3fb81e8d37d0c7386fce324ff445eab3c4170a3e94a97504a775f` and records image size `12254459362`, Dockerfile digest `6c2f04239e6d281d154ec47889ffe1cd152824bdc31de8ce34fc288af8b2d62b`, benchmark commit `b91d4757aa0d7776c02540c9089df54fa0d0658a`, and profile-lock digest `548b899107a895124083a0018b1204c0079e2f8b5f44d02f57056f30a50f038d`. PR `#1283` merged the manifest pin as `6f7cf2222d39f4b1add5af94e8f0fe25eb0dbb99`; protected CI `32654495800` and deployment `32654495815` independently verified the manifest, completed both exact container rollouts, and passed disabled health with both review digests zero. Immutable accepted-archive run `32655461005` then passed exact health, State/archive binding, one-use unwrap and reuse refusal, AWS-authority removal, decrypt, blocked egress, x86_64 AMD EPYC / kernel `6.18.36-cloudflare-firecracker-2026.6.17`, and destruction. Its source-free artifact `9497333640` has file SHA-256 `c5aff4d7c55c3d2bb68e14b758d219659f2c34b8d0679f9d764e0bbc78ebda9f`. PR `#1284` merged the deterministic freeze as `dec6013da9a9678ee5188108bd79b1a5a89ab475`: execution profile `271b407bad361b969ffb0fab42d8bf3615377b08adbcedb825d6e6ac1d905c06`, unchanged measurement config `2dfc898270b83b6c99689e3f551a102c5e76636ec9f469a408498080e3e45945`, and configuration file SHA-256 `391f2938754f251e51468c9244a5ec453174944d1f76c62252f9832be14dcb4b`. Protected CI `32655853393` and deployment `32655853415` passed exact disabled health and rollout gates. Staging State PR `#11` appended reviewed reconfiguration event `01a02fbe-04e0-71b0-a9c5-e9e559ed978e` after attempt 3 and merged as `3e61c3b1e708955eba26d917288f790fa30afd28`; post-merge validator `32656202180` passed. The queue preserves retryable attempt 3 and now binds the Python 3.11 profile. Production replay and both intake paths remain disabled. |
| Python 3.11 authoritative replay attempt 4 | 2026-08-23 | PR `#1285` enabled only staging general replay as `12dff4223b15b483cf9ad8028aed33fcf135ffcc`; protected deployment `32656392560` passed exact image rollout and enabled staging health while production replay and both intake paths remained disabled, and CI `32656392565` passed. Immutable controller run `32656576985` appended attempt-4 start event `01a02fc6-e8c0-72fa-9b36-edfb49b3415c` at State commit `28f9a47769959bee16a39d3964e5a3198745bbe4`, consumed and rejected reuse of one exact unwrap capability, removed AWS authority, invoked the reviewed Python 3.11 executor, and durably recorded terminal `replay.crashed` event `01a02fc8-11a0-70d6-a0f7-6ccf1f882b61` at State commit `3782d7c2680aea2faf9fbd2019556e82e07a95e8`. Source-free artifact `9497604468` has file SHA-256 `fc563cb1175342fd8455341e482e097aebfd701fc26cdb0defa87d1b81d0ba41` and binds exact health/profile/image, confirmed destruction, one-file/eight-line archive statistics, zero-millisecond build/check times, and unavailable instruction counters. Live Cloudflare tail independently recorded successful destruction with zero mounts in 521 ms. No new result, release, production authority, or intake enablement was written. PR `#1286` returns staging general replay to disabled configuration pending diagnosis of the terminal crash. |
| Runtime-source authoritative image publication and qualification | 2026-08-24 | PR `#1287` merged the missing `LeanEval` / `EvalTools` runtime sources, explicit offline Lake package identity, pre-measurement evaluator classification, and regression gates as `b069186599b79a52f1282e8ddb3eb3c8f7d3fc64`. Pull-request image run `32671142054` built the exact image and proved both the root evaluator targets and `generated/two_plus_two` build with `--network none` after all Git metadata was removed; protected CI `32674088263` and intake-disabled staging/production deployment `32674088295` passed. Protected publication run `32674459852` attempt 2 repeated the build, offline-source, command, Python 3.11, and 20 GiB gates, pushed immutable tag `lean-eval-authoritative:b069186599b79a52f1282e8ddb3eb3c8f7d3fc64`, and resolved manifest `sha256:3b573d8ffdab712afba46a13f7f05e844b0365f00f7e7b7f734b5211f4ef9624`. Source-free artifact `9503291836` has file SHA-256 `6670628125b95b937feee27de17f9f0971bc29851c6c00d59f2a288599fe3214` and records image size `12256068438`, Dockerfile digest `ee3faf4e02f6ee90765b6df35ef3b4d4f05daacd2d0b56034ba67055e0a2d812`, unchanged benchmark commit `b91d4757aa0d7776c02540c9089df54fa0d0658a`, and unchanged profile-lock digest `548b899107a895124083a0018b1204c0079e2f8b5f44d02f57056f30a50f038d`. Attempt 1 stopped before publication on a transient `sum.golang.org` TLS timeout. PR `#1288` then pinned that manifest with replay disabled and both review digests zero; protected run `32678416383` independently resolved the manifest, completed both container rollouts, and passed exact disabled health. Immutable accepted-archive run `32679383977` qualified withheld submission `01a02c83-79f7-730b-9bcd-8cdac4fa5d7a`, one-use unwrap/reuse refusal, AWS-authority removal, decrypt, blocked egress, x86_64 AMD EPYC / kernel `6.18.36-cloudflare-firecracker-2026.6.17`, and confirmed destruction. Its source-free artifact `9503677260` has file SHA-256 `47b3e18f1e41295b7a118bde2e398f67a2171c25af313fa8701feaa5ab832eba`. The frozen execution profile is `96457c9d23674faafea669103ac6336b3945999df54d67446c2cc6cc479a9f44`, the unchanged measurement configuration is `2dfc898270b83b6c99689e3f551a102c5e76636ec9f469a408498080e3e45945`, and configuration file SHA-256 is `67426e6b41c5b4ac075abd20f8a1321e2bc8da79a55a6e69731a7351b780c595`; replay remains disabled pending merge and State enqueue. |
| Runtime-source replay freeze and enqueue | 2026-08-24 | PR `#1289` merged the qualified configuration as `eb54be29486bbd4c6d29fb90c357bf37b253bd78`; CI `32679682650` and protected deployment `32679682639` passed exact staging/production health with replay disabled and all three reviewed digests bound. Staging State PR `#12` appended `replay.enqueued` event `01a03162-358c-72a9-8b46-cdcfa1ba45d9` and squash-merged as `b6c1508a12ed571af335e628e1acca27ea9601da`; post-merge validator `32679976816` passed. The new attempt-0 task `rt1_b9c5464f449f42ef58b82f94eca675d85b930f43d1112da20e73d629770f2db6` binds withheld result `r2_99df81809318fd2673d82da042b451f77b55606c6b506beb4526828ee1e7079e`, exact accepted archive commit/ciphertext, execution profile `96457c9d23674faafea669103ac6336b3945999df54d67446c2cc6cc479a9f44`, and measurement configuration `2dfc898270b83b6c99689e3f551a102c5e76636ec9f469a408498080e3e45945`. Production replay and both intake paths remain disabled; staging replay enablement is a separate one-run gate. |
| Runtime-source authoritative replay attempt 1 | 2026-08-24 | PR `#1290` enabled only staging general replay as `7b9373f9e2b5a142ffba2cc2d5d2fded759a5990`; protected deployment `32680179100` passed exact staging replay-enabled and production replay-disabled health with both intake paths disabled. Immutable controller run `32680350272` appended attempt-1 `replay.started` event `01a0316a-05b8-79d2-872e-110be30044eb`, consumed and rejected reuse of the exact unwrap capability, removed AWS and State-write authority, and invoked the reviewed runtime-source executor. After 55 minutes 19 seconds the HTTPS connection was reset by the peer before a verdict was returned. The controller failed closed, scrubbed private scratch, and appended retryable `replay.failed` event `01a0319d-17f0-72d5-9343-f534f3885e4a` with reason `runner_lost` at staging State commit `222943c8ef6dd1b650d22653b97ffacabd8db20a`. No terminal verdict, result, release, production authority, or intake enablement was written. Staging general replay is being returned to disabled configuration before diagnosis or retry. |
| Results schema version 2 migration (D7) | 2026-08-22 | maintainer approved fresh dry run `32569220655` at source `ddc0e4ec8980296a5312844dedd5513d1d604e5b`, source digest `884c38373f8ecafbbc3894a6cb90cdca476f558bb32fe44d0af08e8c62fd2e05`, 1,298 records, and canonical output digest `b78fb207d4711c2f59970fd3e769c483cf7eab8f5afb1fec07abe7cadbfc24c4`. Apply run `32569936026` created lock commit `fd1259b3`, rewrote 43 legacy files / 1,088 legacy records, removed the lock, and produced main `c3491661da9dcdad908d1b1e78576d9f64f112f4`. Independent post-apply validation found 44/44 files at schema version 2, 1,298/1,298 records, no duplicates, unchanged canonical output digest, zero further changes, no queued submission writers, and green main CI `32569954466`. |
| Replay decrypt and destruction | 2026-08-23 | synthetic run `32574078784` and real accepted-archive run `32618166048` passed fixed-command decrypt, reuse refusal, egress denial, source-free evidence, and confirmed unconditional destruction; authoritative queue consumption and production replay remain disabled |
| Release reconstruction | 2026-08-22 | protected `lean-eval-releases` run `32574614106` at exact main commit `f1f83344017333650b4066a533e5ff4eefda5b54` passed all tooling tests, planned one due synthetic release, reconstructed and validated its manifest, proved the exact public-file allowlist excludes `private-note.txt`, and left the checkout clean. The run used only a harmless local plaintext fixture: it wrote neither State nor the release repository, exercised no AWS authority, and did not enable publication. |

## Reconciliation checklist

At least quarterly, and after every infrastructure change:

1. compare Cloudflare Worker names, domains, routes, compatibility settings,
   observability, and secrets metadata to this file and `wrangler.jsonc`;
2. compare GitHub environments, secret names, credentials, permissions,
   repository visibility, rulesets, and runner labels to this file;
3. verify staging cannot reach production State and vice versa;
4. compare AWS account, OIDC provider, CloudFormation outputs, KMS aliases and
   rotation, DynamoDB TTL, Lambda aliases/endpoints, IAM trusts/policies, and
   GitHub environment variables to the records above;
5. update `Last reconciled`, owners, identifiers, dates, and deployment results here.
