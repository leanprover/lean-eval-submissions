# LeanEval infrastructure inventory

This file records current externally hosted resources, non-secret identifiers,
credential custody and scope, feature state, and recovery contracts. Secret
values, private keys, recovery material, source bytes, and raw endpoint bodies
must never be recorded here.

Update current values in place after an infrastructure change. Git history and
Actions retain run history; do not add rollout narratives or evidence tables.

Last reconciled: **2026-08-26**

## Current baseline

| Contract | Current value |
| --- | --- |
| Production State contract pin | `b0a30e3a64aa5c05660040405b32135dea4b7f1d` |
| Portable staging State contract pin | `f00055ed2ba9b4252f04e096d27aadd5beef0ed4` |
| Replay image tag | `lean-eval-authoritative:4026b18d5e679b07be1961d538a51ad689a9d8d4` |
| Replay image digest | `sha256:f61b6be446c3bc355c2eefddc3b376226acee89ca562e66f3b283576a32bb20b` |

Public structured health currently reports one coherent deployed commit with:

- production intake configured and effectively disabled;
- staging and production general replay disabled;
- historical-public replay disabled;
- staging acceptance enabled and production acceptance disabled;
- production promotion canary disabled;
- result-owner, amendment-owner, amendment-maintainer, model-owner, and
  model-maintainer APIs disabled; and
- empty maintainer lists.

Automatic release publication is disabled because `PUBLICATION_ENABLED` is
absent. Production archive Wrap and replay role variables are absent. The
staging release OIDC trust repair and credentialed reconstruction remain
approval-gated.

## Cloudflare resources

Temporary owner: Kim Morrison. Intended long-term owner: the Lean organization.
No LeanEval resource is hosted in the unrelated
`Kim@lean-fro.org's Account` (`d789bf36d237e0cb313be59b927c82bd`).

| Field | Value |
| --- | --- |
| Account | `lean-eval` |
| Account ID | `a46b90978a1c29cc4795f30677e7e4b8` |
| Workers subdomain | `lean-eval.workers.dev` |
| Zone | none |
| Billing/cost owner | Workers Paid / Kim Morrison (temporary) |
| Primary administrator | Kim Morrison (`kim@lean-fro.org`) |
| Staging rate-limit namespace | `24012001` |
| Production rate-limit namespace | `24012002` |

No dedicated hostname or DNS change is required for launch. The leaderboard
will provide `https://lean-lang.org/eval/submit/` as a static entry page and
send users to the production Worker origin for authentication and submission.

| Environment | Intake Worker | Broker Worker | Replay Worker / container application |
| --- | --- | --- | --- |
| Staging | `lean-eval-submission-server-staging` | `lean-eval-github-broker-staging` | `lean-eval-replay-executor-staging` / `lean-eval-replay-executor-staging-replaysandbox-staging@22` |
| Production | `lean-eval-submission-server` | `lean-eval-github-broker-production` | `lean-eval-replay-executor` / `lean-eval-replay-executor-replaysandbox-production@25` |

The broker Workers have no public route. Intake and replay health endpoints use
their matching `workers.dev` names. Obtain the active Worker version IDs and
deployed commit from the latest successful protected deployment and structured
health when preparing a rollback or go/no-go packet; copying those ephemeral
values into this file would make this inventory stale during its own
documentation-only deployment.

Declarative configuration is in `server/wrangler.jsonc`,
`server/wrangler.broker.jsonc`, and `server/wrangler.replay.jsonc`. Replay uses
Cloudflare Sandbox SDK `0.12.7` and base image
`cloudflare/sandbox:0.12.7@sha256:6d741713aef266e8ae0831a5709c6f2d7b77b4952ac79b549f4f4e380af86fbe`.
Each environment permits at most one `standard-4` instance (4 vCPU, 12 GiB
RAM, 20 GB disk), with SSH and public network access disabled. Each request
uses a fresh nonce-derived sandbox ID, a fixed command, bounded inputs, and
unconditional destruction.

### Deployment credentials

GitHub environments `cloudflare-staging` (`20259250422`) and
`cloudflare-production` (`20259250928`) are restricted to protected branches.
Each contains distinct values for:

- `CLOUDFLARE_ACCOUNT_ID`;
- `CLOUDFLARE_API_TOKEN`;
- `READINESS_TOKEN`;
- `LIFECYCLE_CALLBACK_TOKEN`.

These GitHub environment copies support deployment and protected verification;
they are not the complete Worker runtime secret set. Each deployed intake
Worker separately has environment-specific Wrangler secrets for:

- `GITHUB_STATE_TOKEN`, scoped only to its matching private State repository;
- `READINESS_TOKEN`;
- `AUTH_TOKEN_SECRET`;
- `LIFECYCLE_CALLBACK_TOKEN`; and
- `GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CLIENT_SECRET` for its matching
  browser OAuth App.

The readiness and lifecycle callback values are the matching values in both
locations. The State token, authentication-signing secret, and OAuth
credentials are Worker runtime secrets and are not copied into the GitHub
deployment environment.

Kim Morrison is the temporary custodian for the runtime secrets below. There
is no alternate custodian recorded. Rotate each environment independently:

| Credential | Current scope | Rotation and revocation |
| --- | --- | --- |
| `READINESS_TOKEN` | One intake Worker's protected readiness endpoints and the matching `cloudflare-*` environment | Replace the Worker secret and matching GitHub environment secret as one approved maintenance change, verify readiness, and retain no old value. Overwriting the Worker secret revokes the old token. |
| `LIFECYCLE_CALLBACK_TOKEN` | One intake Worker's lifecycle callbacks and the matching source-free callback jobs | Replace both matching copies as one approved maintenance change, verify a source-free callback denial/success pair, and retain no old value. Overwriting the Worker secret revokes the old token. |
| `AUTH_TOKEN_SECRET` | Session signing for one intake Worker only | Replace only the matching Worker secret. This intentionally invalidates all sessions in that environment; verify new OAuth and agent sessions before reopening intake. Overwriting it revokes every token signed only by the old value. |
| `GITHUB_OAUTH_CLIENT_SECRET` | One environment's personal GitHub OAuth App and matching intake Worker | Generate a replacement in that App, replace only the matching Worker secret, verify OAuth, then revoke the old App secret. |

| Token | Scope | Created / expiry | Rotation and revocation |
| --- | --- | --- | --- |
| `lean-eval-deploy-staging` | Dedicated `lean-eval` account: Workers Scripts Edit and Containers Edit; no zone/DNS permission | 2026-08-21 / none | Kim Morrison; replace only in `cloudflare-staging`, verify, then revoke old token |
| `lean-eval-deploy-production` | Dedicated `lean-eval` account: Workers Scripts Edit and Containers Edit; no zone/DNS permission | 2026-08-21 / none | Kim Morrison; replace only in `cloudflare-production`, verify disabled state, then revoke old token |

Neither token may gain any other account or zone permission. GitHub environment
secrets are unavailable to pull-request jobs.

[`deploy-worker.yml`](.github/workflows/deploy-worker.yml) is the normal
deployment path. It validates code, deploys staging, runs the promotion canary,
then deploys production with tracked capabilities disabled. The protected
`submission-dispatch-promotion` environment (`20259251430`) requires reviewer
`kim-em` and contains only `DISPATCH_PROMOTION_APPROVAL_GUARD`. Tag ruleset
`21094118` rejects update or deletion of
`refs/tags/lean-eval-dispatch/*` and has no bypass.

## GitHub applications and State access

### GitHub Apps

| App | App ID / key | Authority | Installation |
| --- | --- | --- | --- |
| Lean Eval Source Reader (`lean-eval-source-reader`) | `4666604` / `4176146` | Metadata read, Contents read | Installed only on contributor repositories that opt in |
| Lean Eval Workflow Dispatcher (`lean-eval-workflow-dispatcher`) | `4666633` / `4176163` | Metadata read, Contents read, Actions read/write | `155329316`, selected repository exactly `leanprover/lean-eval-submissions` |

Both Apps are owned by `leanprover`, subscribe to no events, and have no
webhook. Each broker environment holds its matching App IDs and private keys.
Rotate a key by installing the replacement in both broker environments,
verifying staging then disabled production, and deleting the old key. Do not
grant gist or broad repository authority.

### Browser OAuth Apps

Temporary owner: personal account `kim-em`, which is acceptable for initial
launch. Recovery and rotation remain with Kim Morrison until transfer. The
current recovery path is recovery of the `kim-em` GitHub account; there is no
independent alternate OAuth-App custodian. If that account is unavailable,
pause browser intake rather than changing callbacks or credentials without a
new approval. The intended later transfer is both Apps to `leanprover`, keeping
the same exact callbacks and `read:user` scope, followed by environment-by-
environment client-secret rotation.

| App | Application ID / client ID | Exact callback |
| --- | --- | --- |
| Lean Eval Submissions (staging) | `3806355` / `Ov23li6zjHADKyrgKeRa` | `https://lean-eval-submission-server-staging.lean-eval.workers.dev/api/v1/oauth/callback` |
| Lean Eval Submissions | `3806359` / `Ov23liFcOLHsyvY9DmQ5` | `https://lean-eval-submission-server.lean-eval.workers.dev/api/v1/oauth/callback` |

Callbacks use exact matching, device flow is disabled, user-token expiry is
enabled, and the Worker requests only `read:user`. Rotate a client secret by
replacing it only in the matching Worker environment and verifying OAuth before
revoking the old secret. Organization transfer is a later ownership task, not
a launch gate.

### State writer credentials and rulesets

Both State repositories are private append-only ledgers.

| Field | Staging | Production |
| --- | --- | --- |
| Repository | `leanprover/lean-eval-state-staging` | `leanprover/lean-eval-state` |
| Ruleset | `21094006` | `21094005` |
| Fine-grained PAT | `lean-eval-state-writer-staging` (`18528992`) | `lean-eval-state-writer-production` (`18529041`) |
| Scope | Metadata read, Contents read/write on this repository only | Metadata read, Contents read/write on this repository only |
| Created / expires | 2026-08-21 / 2026-11-19 | 2026-08-21 / 2026-11-19 |
| Rotation owner / deadline | Kim Morrison / 2026-11-05 | Kim Morrison / 2026-11-05 |

Rulesets reject deletion and non-fast-forward history and require strict
`validate`. The recorded `kim-em` user bypass supports the two bootstrap PATs;
remove it when those credentials retire. The internet-facing Worker must not
write workflows, repository settings, the submissions repository, or the other
environment's State.

### Deploy keys

| Use | Key IDs / scope |
| --- | --- |
| Leaderboard production State projection | `160968617`, read-only production State |
| Release staging | audit `161041215` as `AUDIT_READ_KEY`; State `161041214` as `STAGING_STATE_READ_KEY` |
| Release production | audit `161041000`; State controller `161040898`; release publisher `161040897` |
| Historical migration | audit `161041934`; read-only |
| Accepted-archive replay | State `161043118` as `STAGING_STATE_READ_KEY`; audit `161043119` as `AUDIT_READ_KEY`; read-only |
| Staging authoritative replay | State writer `161051584`; private half only as `STAGING_STATE_WRITE_KEY` in `replay-staging` |

Production release keys are installed, but publication remains impossible
without the separate absent publication variable. Rotate or revoke a deploy key
in its owning repository and matching protected environment; never reuse one
key across roles. Kim Morrison is the temporary operator for this deploy-key
set. Rotation means adding a replacement public deploy key with the same
read-only/read-write bit, replacing only the matching environment's private-key
secret, verifying the protected read or disabled publication path, and deleting
the old deploy key from its owning repository. For immediate revocation, delete
the public deploy key first and then remove the matching environment secret.

## GitHub environments

| Repository / environment | Node | Ref policy | Current external authority |
| --- | --- | --- | --- |
| submissions / `archive-staging` | `EN_kwDOSh7OzM8AAAAEu8r2_A` | `lean-eval-dispatch/*` | `AWS_WRAP_ROLE_ARN` set to staging Wrap role |
| submissions / `archive-production` | `EN_kwDOSh7OzM8AAAAEu8r25w` | `lean-eval-dispatch/*` | Production Wrap variable absent |
| submissions / `replay-staging` | `EN_kwDOSh7OzM8AAAAEu8r21Q` | `main` and `lean-eval-dispatch/*` | Staging replay invoker role set |
| submissions / `replay-production` | `EN_kwDOSh7OzM8AAAAEu8r3MQ` | protected branches | Production replay variable absent |
| submissions / `archive-migration-production` | `EN_kwDOSh7OzM8AAAAEwLDSMQ` | protected branches | Ordinary production Wrap role recorded but incompatible with this environment's OIDC subject; unusable |
| releases / `release-staging` | `EN_kwDOT-oWes8AAAAEu8r3Mw` | protected branches | Staging release invoker role set; live trust repair pending |
| releases / `release-production` | `EN_kwDOT-oWes8AAAAEu8r3KQ` | protected branches | Production release invoker and Git keys set; live release trust repair pending; publication variable absent |

Environment protection/policy IDs, in the same order, are:

- `archive-staging`: protection `63321649`, tag policy `57914845`;
- `archive-production`: protection `63321647`, tag policy `57914846`;
- `replay-staging`: protection `63352004`, branch policy `57941304`, tag
  policy `57941307`;
- `replay-production`: protection `63321654`;
- `archive-migration-production`: protection `63434355`;
- `release-staging`: protection `63321653`; and
- `release-production`: protection `63321651`.

Never configure archive environments as protected-branches-only: dispatch runs
from immutable tags. Never allow unrestricted refs. Changes to environments,
variables, credentials, deploy keys, or rulesets require explicit maintainer
approval.

## AWS archive-key boundary

AWS supplies archive key custody and one-use unwrap, not evaluation compute.

| Field | Value |
| --- | --- |
| Account | `lean-eval` / `161072922960` |
| Region | `us-east-1` |
| Root/contact | `kim+lean-eval@lean-fro.org`; MFA enabled; no root access key |
| Billing/administrator | Kim Morrison (temporary) |
| GitHub OIDC provider | `arn:aws:iam::161072922960:oidc-provider/token.actions.githubusercontent.com` |
| OIDC audience / thumbprint | `sts.amazonaws.com` / `ab9d0263244dd0326eb67015705a667e79cfe998` |
| Staging stack | `lean-eval-key-adapter-staging`; `CREATE_COMPLETE`; `2251e410-9e15-11f1-a8ef-0eba172391bd` |
| Production stack | `lean-eval-key-adapter-production`; `CREATE_COMPLETE`; `6ab5d7c0-9e15-11f1-9a35-0affda52f513` |

| Output | Staging | Production |
| --- | --- | --- |
| Adapter | `aws-kms-v1` | `aws-kms-v1` |
| KMS key | `arn:aws:kms:us-east-1:161072922960:key/7e15960c-7de0-43ac-bb42-e31683cbea9f` | `arn:aws:kms:us-east-1:161072922960:key/219904f9-4952-400f-b60a-6f027c4d070b` |
| Alias | `alias/lean-eval-archive-identities-staging` | `alias/lean-eval-archive-identities-production` |
| One-use table | `lean-eval-capability-consumption-staging` | `lean-eval-capability-consumption-production` |
| Unwrap alias | `arn:aws:lambda:us-east-1:161072922960:function:lean-eval-archive-unwrap-staging:live` | `arn:aws:lambda:us-east-1:161072922960:function:lean-eval-archive-unwrap-production:live` |
| Archive Wrap role | `arn:aws:iam::161072922960:role/lean-eval-archive-wrap-staging` | `arn:aws:iam::161072922960:role/lean-eval-archive-wrap-production` |
| Replay invoker | `arn:aws:iam::161072922960:role/lean-eval-replay-unwrap-invoker-staging` | `arn:aws:iam::161072922960:role/lean-eval-replay-unwrap-invoker-production` |
| Release invoker | `arn:aws:iam::161072922960:role/lean-eval-release-unwrap-invoker-staging` | `arn:aws:iam::161072922960:role/lean-eval-release-unwrap-invoker-production` |
| Function role | `lean-eval-archive-unwrap-function-staging` | `lean-eval-archive-unwrap-function-production` |

Both customer-managed KMS keys have annual rotation. Both one-use tables use
on-demand capacity, server-side encryption, and `expires_at_epoch` TTL. Lambda
`live` aliases point to immutable version `1`; functions have no public URL or
API Gateway. The conditional DynamoDB insert on `capability_digest` occurs
before decrypt and enforces reuse refusal.

Current GitHub OIDC subject prefixes are
`repo:leanprover/lean-eval-submissions` and
`repo:leanprover@7233018/lean-eval-releases@1340741242`. The live release roles
still trust the obsolete release subject. Their separately approval-gated
repairs are documented in
[`docs/aws-release-staging-trust-repair.md`](docs/aws-release-staging-trust-repair.md)
and
[`docs/aws-release-production-trust-repair.md`](docs/aws-release-production-trust-repair.md).
The production procedure reuses the live template so it cannot also provision
the deferred migration role. Neither repair is authorized by this inventory.

The historical migration role
`arn:aws:iam::161072922960:role/lean-eval-archive-migration-wrap-production`
and stack output `MigrationWrapRoleArn` are not provisioned. The existing
ordinary production Wrap role does not trust the migration environment. The
required `LEGACY_ARCHIVE_IDENTITY` secret is absent.

Archives remain standard `age` ciphertext. The schema-version-3 sidecar binds
submission ID, ciphertext digest, recipient, adapter name, and opaque wrapped
identity. Provider-specific account, region, ARN, encryption-context, and SDK
fields remain inside the adapter payload and must not enter stable IDs or
generic capability claims. New archives use
`archives/<first-two-submission-UUID-hex>/<submission-UUID>.tar.age`.

## Public State and releases

Raw State and `materialized/domain.json` remain private. The leaderboard uses
read-only key `160968617` to generate and validate the redacted public State
projection. Pull-request builds do not receive the key. Revoking it stops the
read path without affecting State writers or intake.

`leanprover/lean-eval-releases` owns public two-calendar-month-delayed source
bundles and provenance. Eligibility is recomputed from immutable State. The
controller's publication variable remains absent. Release opt-out and
submitter-facing license/release text must pass the launch smoke and maintainer
review before enablement.

## Monitoring and emergency response

[`lifecycle-readiness-monitor.yml`](.github/workflows/lifecycle-readiness-monitor.yml)
checks all public Worker health endpoints and binds the reported commit to the
latest successful protected deployment and immutable dispatch tag. An active
rollout is suppressed only for a bounded grace period; an older active rollout
is `deployment_rollout_stuck`.

The canonical bot-owned incident is
`leanprover/lean-eval-submissions#1310`. Temporary severity, support, and
emergency-pause owner: Kim Morrison (`@kim-em`). When intake is enabled, a
readiness failure is severity 1. Pause intake first through the protected
disable path, verify effective disabled health, preserve State/ref evidence,
then forward-deploy or complete one reviewed rollback unit.

## Recovery contract

Use [`rollback-worker.yml`](.github/workflows/rollback-worker.yml) with one
reviewed target commit and its exact intake, broker, and replay version IDs.
The workflow redeploys exact target code/configuration while retaining current
secret values; it never activates an old Cloudflare version directly. Replay
is restored from the target's reviewed container image digest. Rollback is
disable-only and must finish with production intake effectively disabled.

Rollback does not revert or rewrite State, Results, releases, AWS resources,
credentials, or repository history. If the non-atomic multi-component deploy
is interrupted, keep intake paused and rerun disable-only recovery or
forward-deploy one coherent reviewed unit. Never mix target commits.

Release-removal and confidentiality recovery use the contracts in
`leanprover/lean-eval-releases`:

- `docs/release-controller-contract.md`; and
- `docs/release-confidentiality-incident-recovery.md`.

## Canonical retained inputs

The following remain operational inputs and must not be replaced by prose
summaries:

- `.audit/recipients.txt` and `.audit/cloudflare-rollback-qualification-v1.json`;
- schemas under `schemas/`;
- historical inventory, plan, toolchain, profile, and unavailability objects
  under `evidence/historical-replay/` and `evidence/public-replay/`;
- `docs/historical-replay-inventory.md`;
- `docs/historical-public-replay-plan.md`;
- `docs/historical-public-replay-profiles.md`;
- `docs/historical-private-archive-crosswalk.md`;
- `docs/historical-public-unavailability.md`; and
- current setup and trust-repair instructions under `docs/`.

## Reconciliation checklist

At least quarterly and after every infrastructure change:

1. compare Cloudflare accounts, Worker names and versions, routes, container
   image/digest/profile, feature flags, observability, and secret names;
2. compare GitHub environments, ref policies, credential names/scopes/expiry,
   deploy keys, repository visibility, rulesets, and rotation ownership;
3. verify staging cannot reach production State and vice versa;
4. compare AWS account, OIDC subjects, stack outputs, KMS aliases and rotation,
   one-use tables, Lambda aliases, and IAM trust/policy boundaries;
5. verify production archive Wrap, replay, publication, and public lifecycle
   gates remain absent or disabled until separately approved; and
6. update `Last reconciled` and current values here without adding run history.
