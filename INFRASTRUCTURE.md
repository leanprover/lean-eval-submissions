# lean-eval infrastructure inventory

This file is the source of truth for externally hosted lean-eval
infrastructure. A change to Cloudflare, GitHub credentials, deployment credentials,
state repositories, runner topology, DNS, or release storage is incomplete
until this ledger changes in the same pull request or an immediately linked
operations pull request. Secret **names, owners, scopes, and rotation dates**
belong here; secret values do not.

Last reconciled: 2026-08-25 (protected production State `main` and the reviewed
result-owner/model-identity contract are exactly
`6799522f7fe57263de4a66499e52ce4bfda69baa`; protected staging State `main` is
`a1ebcd61a2796dea892703a82396fb374aa38820`, which descends exactly from portable
contract `9fc7c431a92c678554c65ebac68d3fddf4990d29` while preserving its reviewed
README, docs, schema, and scripts root entries. The tracked Worker configuration,
production readiness proof, deploy controller, rollback qualification, and
result-owner/model-identity pins use that coherent contract pair. The atomic
complete-graph producer is deployed from exact commit `0d52fb663d6fe09ae56caf5b007a12e2c5e2c5b5`
with every result-owner and model-identity owner/maintainer gate false in both
environments and both maintainer lists `[]`;
State-writer tokens, browser OAuth Apps, and both broker GitHub
Apps remain provisioned and preflighted; the dedicated AWS key-custody account and isolated
staging and production stacks are provisioned; archive/replay staging and both
release role variables are connected; the historical migration environment's
current ordinary production Wrap role is incompatible with its exact OIDC
subject and awaits a dedicated role/output/variable replacement; the
lifecycle-aware leaderboard cutover is live; D7
migrated all 44 results files and 1,298 records to schema version 2 at commit
`c3491661`; the automatic release controller and recovery tooling are merged
through current `lean-eval-releases` main `90dadc872d624b8e6d171caf439313d185fc3e7f`; current runtime rollout
`32831767076`, readiness recovery `32832326810`, publication-disabled Git credential preflight
`32723471497`, and post-merge release validation `32832191302` passed; the credentialed staging unwrap and live AWS trust update
remain pending, and publication remains disabled).
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
| GitHub release repository | `leanprover/lean-eval-releases` | production | **HARDENED AUTOMATIC CONTROLLER THROUGH `90dadc87` MERGED; POST-MERGE VALIDATION AND PINNED-STATE INTEGRATION PASSED; CREDENTIALED STAGING TRUST UPDATE PENDING; PUBLICATION DISABLED** |
| GitHub branch ruleset | `lean-eval-generator` `Protect main` (`21094079`) | shared | **ACTIVE; PR + LINEAR HISTORY + `check` REQUIRED; APPROVAL COUNT 0** |
| GitHub branch ruleset | `lean-eval-releases` `Protect main` (`21094082`) | production | **ACTIVE; PR + LINEAR HISTORY + `validate` REQUIRED; APPROVAL COUNT 0** |
| GitHub Environment | `cloudflare-staging` (`20259250422`) | staging | **CREATED 2026-08-20; ACCOUNT ID SET; API TOKEN SET 2026-08-21** |
| GitHub Environment | `cloudflare-production` (`20259250928`) | production | **CREATED 2026-08-20; ACCOUNT ID SET; API TOKEN SET 2026-08-21** |
| GitHub Environment | `submission-dispatch-promotion` (`20259251430`) | shared | **CREATED 2026-08-20; REVIEW + GUARD CONFIGURED** |
| GitHub Environment | `archive-staging` (`EN_kwDOSh7OzM8AAAAEu8r2_A`) | staging archive | **CREATED; TAG POLICY + WRAP ROLE ARN SET** |
| GitHub Environment | `archive-production` (`EN_kwDOSh7OzM8AAAAEu8r25w`) | production archive | **CREATED 2026-08-21; TAG POLICY SET; ROLE ARN NOT SET** |
| GitHub Environment | `replay-staging` (`EN_kwDOSh7OzM8AAAAEu8r21Q`) | staging replay | **CREATED; MAIN + DISPATCH TAG POLICIES + INVOKER ROLE ARN SET** |
| GitHub Environment | `replay-production` (`EN_kwDOSh7OzM8AAAAEu8r3MQ`) | production replay | **CREATED 2026-08-21; PROTECTED BRANCHES ONLY; ROLE ARN NOT SET** |
| GitHub Environment | `archive-migration-production` (`EN_kwDOSh7OzM8AAAAEwLDSMQ`) | historical migration | **CREATED 2026-08-23; READ KEY SET; CURRENT ORDINARY PRODUCTION WRAP ROLE CANNOT TRUST THIS ENVIRONMENT; DEDICATED ROLE/VARIABLE REPLACEMENT + LEGACY IDENTITY PENDING** |
| GitHub Environment | `release-staging` (`EN_kwDOT-oWes8AAAAEu8r3Mw`) | staging release | **ROLE + READ KEYS SET; LIVE OIDC TRUST UPDATE PENDING** |
| GitHub Environment | `release-production` (`EN_kwDOT-oWes8AAAAEu8r3KQ`) | production release | **ROLE + CONTROLLER/PUBLISH KEYS SET; PUBLICATION VARIABLE ABSENT** |
| AWS account | `lean-eval` (`161072922960`) | dedicated key custody | **CREATED; ROOT MFA ENABLED; NO ACCESS KEYS** |
| AWS CloudFormation stack | `lean-eval-key-adapter-staging` | staging | **PROVISIONED; RELEASE OIDC TRUST UPDATE PENDING** |
| AWS CloudFormation stack | `lean-eval-key-adapter-production` | production | **PROVISIONED; RELEASE OIDC TRUST UPDATE PENDING; INTAKE/REPLAY/PUBLICATION DISABLED** |
| Cloudflare replay Worker | `lean-eval-replay-executor-staging` | staging | **CORRECTED IMAGE ACCEPTANCE PASSED 2026-08-23; BACKGROUND-PROTOCOL IMAGE/PROFILE FROZEN; REVIEWED STATE RECONFIGURATION RECORDED; REPLAY DISABLED** |
| Cloudflare replay Worker | `lean-eval-replay-executor` | production | **PROVISIONED 2026-08-22; REPLAY AND ACCEPTANCE DISABLED** |
| Replay execution backend | Cloudflare Sandbox, provider-neutral adapter | staging / production | **BACKGROUND-PROTOCOL IMAGE/RUNTIME PROFILE FROZEN; STAGING/PRODUCTION REPLAY DISABLED** |

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
`0d52fb663d6fe09ae56caf5b007a12e2c5e2c5b5`:

| Environment | Private broker version | Replay Worker version / container application / digest | Intake Worker version | Health verification |
| --- | --- | --- | --- | --- |
| staging | `f9091d2a-7a2a-474d-ad86-77c66d9da399` | `ba1160d4-1de1-450a-aaa2-44e1cb040904` / `lean-eval-replay-executor-staging-replaysandbox-staging@22` / `sha256:f61b6be446c3bc355c2eefddc3b376226acee89ca562e66f3b283576a32bb20b` | `cbb48cb8-311d-466b-b364-7cffd86bfed9` | environment `staging`, intake `false`, replay `false`, acceptance `true`, legacy, amendment-owner, amendment-maintainer, model-owner, and model-maintainer APIs `false`, canary `true`, both memory fields `12884901888`, exact commit |
| production | `746cce34-53ba-4332-90dc-88c074f2598a` | `30fcb137-4bfd-48fb-acfd-e26bcf00c64f` / `lean-eval-replay-executor-replaysandbox-production@25` / `sha256:f61b6be446c3bc355c2eefddc3b376226acee89ca562e66f3b283576a32bb20b` | `d8b0133e-f2ce-4452-947f-62c9723a3b86` | environment `production`, intake `false`, replay `false`, acceptance `false`, legacy, amendment-owner, amendment-maintainer, model-owner, and model-maintainer APIs `false`, canary `false`, both memory fields `12884901888`, exact commit |

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
deployment path. A change to the Worker, submission workflow, trusted runtime
workflow scripts, or archive recipient set merged to protected `main` runs:

1. locked dependency install, generated binding types, typecheck, lint, tests,
   dependency audit, and Wrangler dry run;
2. staging broker, replay Worker/container, and intake Worker deploys, followed
   by exact structured `GET /healthz` smoke tests;
3. production broker, disabled replay Worker/container, and intake Worker
   deploys, followed by the same exact health gates.

Workflow-only operations that require an immutable dispatch tag but do not
change deployed runtime are promoted separately by
[`promote-workflow-dispatch-ref.yml`](.github/workflows/promote-workflow-dispatch-ref.yml).
That path uses the same protected promotion environment and collision-safe tag
contract, but it cannot deploy a Worker or container. The live-commit-bound
`set-staging-intake.yml`, `accepted-archive-replay-staging.yml`, and
`authoritative-replay-staging.yml` workflows instead use the ordinary
staging/canary/production rollout. After launch, changing one of those three
may provisionally disable intake during that rollout and restores the tracked
intake state only after every production verification succeeds.

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
then production only after the exact staging promotion canary succeeds. That
readiness-authenticated gate binds the deployed commit to its immutable
dispatch tag, checks the private synthetic fixture through the staging
source-reader broker, records a deterministic withheld intake in staging State,
requires a real non-fast-forward State CAS rejection and retry, and waits for
the actual Cron Trigger to reconcile the resulting outbox through the dispatch
broker. `PROMOTION_CANARY_ENABLED` is tracked `true` only in staging and
explicitly `false` in production; the route also requires the staging runtime,
staging State repository, ordinary intake disabled, and the exact commit-named
dispatch ref. The existing environment-scoped `READINESS_TOKEN` authenticates
the gate, so no new secret or production canary authority is created.
Deployment workflow concurrency is intentionally latest-main-wins: skipped
intermediate commits are already ancestors of the latest tested commit.

Staging intake state is changed only through the protected, manual
`Set staging intake` workflow. The operator must select an exact immutable
`lean-eval-dispatch/<commit>` tag for the commit reported by the live staging
health endpoint, provide the same full commit, and choose `enabled` or
`disabled`. On the current workflow copy, a branch selection or a stale tag
fails the run; it cannot report a successful no-op or roll staging back.
Immutable tags created before commit `c07e002b631520784e8538a205b596e8b9bc714f`
retain their historical workflow copy and can still report a non-failing skipped
job, so operators must never select them. The workflow requires the selected
tag to resolve to the selected commit,
deploys only the staging intake Worker, and verifies the resulting structured
health response. It cannot target production. Any later ordinary main
deployment returns staging to the tracked safe default `INTAKE_ENABLED=false`;
rerun the manual workflow after reviewing the newly deployed commit if staging
testing should continue.

Production has no manual intake-state workflow. Its only launch toggle is the
reviewed `server/wrangler.jsonc` production `INTAKE_ENABLED` value, which
remains `false`. After every production launch gate is recorded, the launch PR
must set that tracked production value to `true`, regenerate the checked-in
Wrangler types, and update the focused production expectation in
`tests/test_worker_intake_configuration.py`; it must make no other semantic
change. Staging stays at its tracked `false` default and keeps using the
protected manual workflow above.
The ordinary production deployment derives its expected state from the same
reviewed configuration, but never exposes a tracked enabled intake before its
dependencies are qualified. It first deploys the reviewed production intake
code with a forced `INTAKE_ENABLED=false`, verifies that exact 100%-active
version and disabled health, verifies the exact broker and disabled replay
versions and replay health, and snapshots the exact protected State commit.
Only when the tracked value is `true` does it create an authenticated,
one-use request bound to the exact controller commit, run ID and attempt,
target commit, production environment, and reviewed State commit. It deploys
the target code in `leased` mode for at most fifteen minutes. The Worker checks the
lease on every intake request and becomes effectively disabled at the exact
expiry second even if GitHub Actions is cancelled, times out, or never starts
again. The controller verifies the exact 100%-active leased version and public
configured/effective health, consumes the bound nonce with an exact-head State
CAS, and proves protected State did not move. Only then is the same reviewed
code deployed in `durable` mode. That durable deployment is the final atomic
step: it carries no lease variables and has no risky follow-up work.

GitHub cancels in-job cleanup on force-cancel and can leave a separate workflow
queued forever, so Actions is not the safety boundary. The finite Worker lease
is. A failed or abandoned controller can leave at most a self-expiring leased
version; it cannot create durable enablement because durable deployment follows
all smoke and State proofs and is the last operation. The protected
[`intake-disable-recovery.yml`](.github/workflows/intake-disable-recovery.yml)
is defense-in-depth cleanup only. It automatically follows failed protected-main
deployments and can also be dispatched manually without user-supplied run
pairing. It derives and verifies the exact controller identity and tag (the
automatic path accepts only a failed controller; manual emergency disable may
also target the latest successful controller),
can deploy only `INTAKE_ENABLED=false`/`disabled`, and verifies the exact active
version and public disabled health. Before mutation it requires public health
to prove production still runs that exact controller commit with intake
configured on; a staging-only failure or superseded controller is a no-op. It
contains no enable path.

Operational prerequisites remain important for prompt cleanup: the recovery
workflow must stay enabled on protected `main`, `cloudflare-production` must
admit its disable-only job, and the job needs the existing production
Cloudflare environment secrets. A queued, cancelled, or failed cleanup never
extends the lease; the Worker independently fails closed at expiry. Verify
public health and run the disable-only recovery before any later production
change if a controller does not complete.

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

The retired staging amendment canary used a distinct one-use credential and
never accepted `READINESS_TOKEN`. After successful run `32793103590`, the
GitHub environment copy was deleted and the Worker copy was rotated to an
unknown value. The cleanup deployment removes the route, binding, generated
type, required-secret declaration, workflow, and test fixture before the
remaining Worker-side secret metadata is deleted.

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
that last sentence: it is an operator-only credential check that works before
or after intake launch. It reads the State branch and submits a non-forced
same-commit ref update, proving both Contents-write authority and the configured
ruleset bypass without changing the branch, commit graph, or State tree. The
protected workflow still requires the exact ready status, selected environment,
and canonical State commit, accepts only a JSON boolean intake state, and
reports whether the live Worker was enabled or disabled instead of requiring a
particular state.

The authenticated staging-only `POST /internal/v1/source-reader-preflight`
performs one repository-metadata read through the private Source Reader broker.
Its protected manual workflow fixes the target to
`kim-em/lean-eval-intake-fixture` and proves the transferred App private key,
installation selection, broker binding, and private visibility without
dispatching or evaluating a submission. Production rejects this preflight.

`DISPATCH_WORKFLOW_REF` must stay absent until an operator creates an immutable
tag named `lean-eval-dispatch/<40-character-commit>` at the reviewed workflow
commit. Runtime deployment and workflow-only promotion jointly own creation.
After its Worker checks and exact protected-main CI, `deploy-worker.yml`
promotes commits that change the running Worker, its directly dispatched
workflows, or the staging-only intake and replay workflows whose preconditions
are bound to the live staging commit. The deployment-free
`promote-workflow-dispatch-ref.yml` path covers tag-consuming operational
workflows, the source-only public-evidence resolver they run, and Results-only
commits that must become exact historical-inventory cutoffs; it waits for exact
protected-main CI and cannot invoke Wrangler or a deployment. Both
minters enter the reviewer-gated
`submission-dispatch-promotion` environment and use only a job-scoped
`GITHUB_TOKEN` with `contents: write` plus read-only Actions access for the
exact-main CI proof. A 32-byte lowercase-hex
`DISPATCH_PROMOTION_APPROVAL_GUARD` secret must exist only in that environment;
it has no external authority and makes missing/unprotected auto-created
environment configuration fail before tag creation. Existing tags are accepted
only when they resolve to the same SHA. Concurrent creation of the same exact
tag is harmless, while collisions and failed read-back stop promotion or
deployment.
A change to any live-commit-bound staging workflow deliberately performs the
ordinary staging/canary/production rollout: this is what makes its new
immutable tag usable. While production intake is tracked disabled, that rollout
resets staging intake to its disabled default and leaves every production gate
disabled. After launch, it may provisionally disable production intake and
restores the tracked enabled state only after the rollout succeeds.
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
it is not the persistence mechanism.

The staging promotion canary is the sole intake-disabled exception to the
ordinary scheduled no-op. Its UUIDv7, nonce, metadata event, acceptance time,
and CAS-evidence event are deterministic for the exact deployed protected-main
commit plus the deployment workflow's `GITHUB_RUN_ID` and
`GITHUB_RUN_ATTEMPT`. A workflow rerun therefore creates fresh material, while
HTTP polling within one attempt reuses the exact State identities. Only the
first call creates and observes the collision/retry proof; later polls report
that the exact proof was already recorded rather than claiming a fresh collision. The
synthetic timestamp is intentionally derived inside a fixed 2026-08-20 UTC
window; it is an identity input, not a claim about wall-clock execution time.

Before recording the evidence event, the State adapter creates two source-free
competing commits from one current branch snapshot, applies the first by a
non-forced update, requires GitHub to reject the second with a real 409/422
non-fast-forward collision, then rebuilds and applies the evidence atop the new
head through the ordinary bounded CAS writer. The response reports the
adapter's observed collision/retry outcome rather than a hard-coded success.
The first commit is an intentional empty-tree barrier: it has a distinct commit
object but preserves the exact State tree. This satisfies the State repository's
full-tree validator, and its append-only CI diff rejects only modified/deleted
event or environment files; the adapter unit test also fixes the base tree and
`force:false` update contract.

All canary UUIDs use the fixed `ca` outbox shard. Each synthetic model string
and outbox workflow ref retain the originating deployed commit, so a later
Worker deployment re-derives and dispatches pending material against its
original immutable tag instead of orphaning it. The one-minute scheduled
entrypoint scans that shard for every strict run-scoped canary, bounds each pass
to 20 due entries, and uses the normal dispatch reconciliation and State-success
update. An exact failed canary that reaches the 32-attempt retry bound is
classified and removed from the outbox rather than retained forever. Per-item
and scan errors emit only source-free classifications and do
not block ordinary staging reconciliation. Dispatch goes through the actual
broker to the immutable-ref `promotion-canary.yml`, whose permissionless job
only validates the source-free run identity. Under that exact dedicated target,
it never runs `submission.yml` and does not write audit archives, evaluator jobs, Results records, or release
events. `dispatch.status=succeeded` and the production gate prove that GitHub
accepted the exact `workflow_dispatch` through broker/reconciliation; they do
not claim that the asynchronous no-op job completed. Responses and workflow logs contain commit/ref, synthetic UUID, run
identity, and categorical results only—never source contents, credentials, or
upstream response bodies. The immutable source pin is the deliberately rejected fixture commit
`ae38f4d3e4ad2991212135435f54e6640bcc89e7`; publication is withheld and the
record is explicitly synthetic, so it cannot become a production benchmark
claim.

The archive job emits a strict completion
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

## Operational monitoring and response

[`lifecycle-readiness-monitor.yml`](.github/workflows/lifecycle-readiness-monitor.yml)
runs on GitHub's best-effort 15-minute schedule and on main-branch manual
dispatch without deployment, State, AWS, archive, release, or source
credentials. The token-free endpoint step derives all four public URLs from the
tracked Wrangler environment names and requires exact environment, service,
enablement, positive safe-integer memory gates, frozen digest formats, and one
full deployed commit. A separate contents-read step requires that commit to be
the uniquely latest-completing successful main-branch protected deployment,
have its exact lightweight `lean-eval-dispatch/<commit>` tag, and remain
reachable from the explicitly protected `main` branch. GitHub reads and issue
mutations use bounded retries within a
four-minute process budget; endpoint response bodies and GitHub error bodies
are never copied into the alert.

Sequential Cloudflare deployment differences are retried locally and then
suppressed only while every queued or running protected deployment is at most
45 minutes old. Suppression neither creates nor closes an incident. Any older
active deployment fails as `deployment_rollout_stuck`, even when the previous
successful deployment remains coherent, and a partial failed rollout with no
active deployment fails immediately. This bound covers observed healthy
container rollouts without allowing a stuck run or a series queued behind one
to mask readiness indefinitely.

The alert destination and support channel are the bot-owned issue titled
`[monitor] LeanEval lifecycle readiness failure` in
`leanprover/lean-eval-submissions`. The workflow creates or reopens that one
marker-bound issue on failure and closes it only after a later complete pass;
it paginates bot-owned issue history to a documented bound (and fails closed if
that bound is exhausted), chooses the oldest exact bot-owned marker as
canonical, and closes any later exact duplicates with a pointer to it. It
never copies raw endpoint bodies, GitHub response bodies, source bytes, or
secrets into the issue. Non-main manual runs are skipped, monitor runs are
serialized without cancellation, and the endpoint process never receives the
issue-write token.

Temporary severity owner, support owner, and emergency intake-pause owner:
Kim Morrison (`@kim-em`). A readiness failure while production intake is
enabled is severity 1; a failure while production intake is disabled is
severity 2 unless confidentiality, credential, or unexpected execution evidence
raises it to severity 1. For severity 1, stop accepting new work first by
returning the tracked production `INTAKE_ENABLED` value to `false` through the
protected deployment path (admin-merge the minimal reviewed change when normal
review latency is unsafe), verify public health reports intake disabled, and
preserve the failed run and State/ref evidence. Then finish the exact reviewed
rollback unit or forward-deploy a coherent fix. State remains append-only;
correct it only with a legal forward event. Record transfer of any of these
temporary ownership roles here before changing the GitHub alert destination.

Operational prerequisites not enforced by repository code: `@kim-em` must
explicitly acknowledge these temporary roles and subscribe to both the
canonical issue and Actions failures before production intake is enabled.
Because GitHub schedules may be delayed, dropped, or disabled and cannot alert
when GitHub Actions itself is unavailable, configure an independent dead-man
check for recent successful monitor runs before treating this workflow as the
production availability pager. Record that check and the ownership
acknowledgement in this ledger.

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
`public-state-projection-v1` contract (schema version 1). Production State PR
`#12` (merge `889e07e3b8cf38ad147d8a23b7d1b35826de740f`) and the
byte-equivalent portable contract in staging State PR `#15` (merge
`494a6746233d3cc3dedc8d9475b74eb30112f860`, identity commit `61385eee`)
add opt-in projection schema version 4. Version 4 retains the v1 result
contract and adds only the public, owner-scoped model-identity decisions,
aliases, rename/consolidation history, and deterministic result resolution
needed by standings consumers. It contains recorded results, public
credit/production metadata, acceptance provenance, replay measurements,
release status, and released-solution links. It omits pending/rejected
submissions, submission IDs, source/archive locators, authentication nonces,
raw events, and private materialized views. Result identities are recomputed,
metadata fields are closed, and the artifact records its exact private State
commit, canonical event digest, and event count.

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
| Historical migration Wrap role ARN | not applicable | **NOT PROVISIONED**; planned output `MigrationWrapRoleArn` for `arn:aws:iam::161072922960:role/lean-eval-archive-migration-wrap-production` |
| Replay invoker role ARN | `arn:aws:iam::161072922960:role/lean-eval-replay-unwrap-invoker-staging` | `arn:aws:iam::161072922960:role/lean-eval-replay-unwrap-invoker-production` |
| Release invoker role ARN | `arn:aws:iam::161072922960:role/lean-eval-release-unwrap-invoker-staging` | `arn:aws:iam::161072922960:role/lean-eval-release-unwrap-invoker-production` |
| Function role | `lean-eval-archive-unwrap-function-staging` | `lean-eval-archive-unwrap-function-production` |

GitHub's repository OIDC API currently reports subject prefixes
`repo:leanprover/lean-eval-submissions` and
`repo:leanprover@7233018/lean-eval-releases@1340741242`. The releases repository
was transferred after GitHub's immutable-subject rollout, so its owner and
repository IDs are part of every token subject even though its display name is
unchanged. The stack template pins these API-reported prefixes separately.
Credentialed release staging runs `32617539355` and `32624640050` proved the
live role still trusted the obsolete name-only release subject: both failed at
STS before Lambda invocation, consumed no capability, and decrypted no source.
The later run had already validated staging State, checked out the exact audit
commit, bound the encrypted object and sidecar, and prepared the unwrap request;
none of those earlier steps supplied AWS authority. Apply the reviewed stack
update before repeating that smoke; do not opt the repository out of immutable
subjects to preserve an obsolete trust policy. The exact staging-only change
set whitelist, verification, smoke, and rollback procedure is recorded in
[`docs/aws-release-staging-trust-repair.md`](docs/aws-release-staging-trust-repair.md).

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
staging and both release environments contain compatible non-secret role
variables. The guarded historical migration environment currently contains the
ordinary production Wrap role ARN, but that role trusts only
`archive-production`; it cannot be assumed from `archive-migration-production`.
The dedicated migration role, `MigrationWrapRoleArn` stack output, and variable
replacement are reviewed configuration only and have not been provisioned or
applied. Production archive and production replay remain unconnected; release
publication remains disabled:

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

The historical migration row records incompatible current state, not usable
authority. After the production stack creates `MigrationWrapRoleArn`, replace
that environment variable with the dedicated output and record the applied
stack and GitHub evidence here. Do not claim the migration lane is connected
before both changes are verified.

Read/write deploy-key inventory: release staging uses audit key `161041215`
and State key `161041214`; release production uses audit key `161041000`,
write-capable State controller key `161040898`, and release publisher key
`161040897`. The production secrets are installed, but no
publication-enabling variable exists.

Protected production preflight `32723471497` ran at exact release-controller
commit `57ab36341ccf653b45366c32d4472b9ee670890b` against exact production
State commit `0c8759946df0da1338a0c73bf5bd75d182038286`. It required
`PUBLICATION_ENABLED` to remain absent or `false`, validated the sole immutable
`system.initialized` State event, materialized all six deterministic State
views, produced a source-free preflight qualification, and reached GitHub's
write-side receive-pack service with both write-capable deploy keys. Both
exact-ref `git push --dry-run` operations reported `[up to date]`. This proves
the current keys are installed and authenticated for the two exact live
repositories; because neither ref needed an update and the pushes were dry
runs, it does not prove a real ruleset-bypass update. The workflow did not use
the audit key, assume an AWS role, invoke Lambda, consume a capability, decrypt
or reconstruct an archive, write an artifact, mutate State or releases, or
publish anything. Production State currently contains no accepted submission
or due release work, so enabling the controller after all remaining gates would
initially be inert; the first later due release would still be the first live
production audit/decrypt/push exercise.

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

Rollback changes only the Cloudflare deployment unit. It does not revert GitHub
State, AWS resources, releases, or other resources. Use the manual
[`rollback-worker.yml`](.github/workflows/rollback-worker.yml) workflow with
the reviewed broker, replay Worker/container, and intake Worker version IDs,
plus the one full commit recorded by all three versions. The historical version
IDs prove the target unit, but they are never activated directly: Cloudflare
rollback can force old secret values after a rotation. Instead, the workflow
builds and fully deploys exact target code/configuration. For the intake Worker,
the exact reviewed `secrets.required` bindings make Wrangler inherit the current
secret values while allowing rollback to clear residual lease variables; the
other components use `--keep-vars`. Before any mutation,
the protected production job proves the immutable dispatch tag, protected-main
reachability, every tracked plain-text binding from that exact commit, the
exact allowed secret/resource capability names and types, unchanged live
resource identifiers, the active live Durable Object migration tag, and the
replay container application. The target commit must carry a reviewed rollback
qualification bound to its exact lifecycle-callback implementation and the
exact schema blob on the current protected production State `main`. Any State
commit/schema movement invalidates an old qualification until it is reviewed
again. This matters even with intake disabled because authenticated archive,
evaluation, and result callbacks can still append State. The target must make
scheduled ordinary-intake reconciliation a no-op while intake is disabled,
retain `PROMOTION_CANARY_ENABLED=false` in production, and track production
replay as disabled. Its reviewed production intake state may be enabled or
disabled. The pre-mutation recovery artifact records the
original three active version IDs, hashed capability contracts, live replay
migration tag, and a closed allowlist of effective container recovery fields.
Raw provider version/status/container responses are not uploaded; the artifact
contains no secret values or Worker/submission source bytes.

The workflow first deploys and verifies the exact target intake code with an
explicit temporary `INTAKE_ENABLED=false` override while retaining current
secret values, so no new submission or scheduled reconciliation can cross the
non-atomic window. It then deploys the matching private broker code with the
same secret-preserving rule. A Worker-version rollback does not change a
connected Cloudflare Container application, so replay is restored by
a full deploy from the exact detached target commit with an immediate container
rollout—not by `wrangler rollback`. Before mutation, the exact registry tag is
resolved to the frozen manifest digest. Afterward, the workflow verifies the
new replay Worker version plus the effective image, instance size, one-instance
limit, SSH-off state, private network, health, and frozen review digests. Only
after those dependencies are exact does it re-check protected State. Rollback
is deliberately disable-only: it never restores a target's enabled intake
state. It leaves the target intake code forced to
`INTAKE_ENABLED=false`/`disabled` and verifies that exact 100%-active version
and public health. A later ordinary protected-main rollout must repeat the
finite-lease smoke before durable enablement. Every Worker must report 100% of
traffic on its selected version; public health must agree with the qualified
target commit and effective disabled state.

Cloudflare cannot atomically change three Workers and a Container application,
and a full Container deploy activates Worker code before its rollout completes.
A failure after the first mutation is therefore an incident. Intake remains
paused throughout rollback. If recovery is interrupted, rerun the protected
disable-only recovery to deploy the reviewed target code with intake forced
off and verify the exact active version, disabled health, and protected State
ancestry. A disabled target has no enabled mutation and is verified directly.
Confirm the controller and any disable-only recovery conclusions and public
intake health, then either finish the reviewed unit or forward-deploy the last
known coherent commit and container target.
Never mix target commits to complete a partial rollback. Never rewrite State to
match an older Worker; deploy a compatibility fix or append a corrective event.

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
