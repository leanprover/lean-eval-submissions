# lean-eval infrastructure inventory

This file is the source of truth for externally hosted lean-eval
infrastructure. A change to Cloudflare, GitHub Apps, deployment credentials,
state repositories, runner topology, DNS, or release storage is incomplete
until this ledger changes in the same pull request or an immediately linked
operations pull request. Secret **names, owners, scopes, and rotation dates**
belong here; secret values do not.

Last reconciled: 2026-08-20 (design committed; resources below are not yet
provisioned). Owner: leanprover organization administrators. Service code:
[`server/`](server/).

## Provisioning status

| Resource | Desired identifier | Environment | Status |
| --- | --- | --- | --- |
| Cloudflare Worker | `lean-eval-submission-staging` | staging | **TO BE PROVISIONED** |
| Cloudflare Worker | `lean-eval-submission-production` | production | **TO BE PROVISIONED** |
| Worker custom domain | `eval-submit-staging.lean-lang.org` | staging | **TO BE PROVISIONED** |
| Worker custom domain | `eval-submit.lean-lang.org` | production | **TO BE PROVISIONED** |
| GitHub state repository | `leanprover/lean-eval-state-staging` | staging | **TO BE CREATED** |
| GitHub state repository | `leanprover/lean-eval-state` | production | **TO BE CREATED** |
| GitHub generator repository | `leanprover/lean-eval-generator` | shared | **TO BE CREATED** |
| GitHub release repository | `leanprover/lean-eval-releases` | production | **TO BE CREATED** |
| GitHub Environment | `cloudflare-staging` | staging | **TO BE CREATED** |
| GitHub Environment | `cloudflare-production` | production | **TO BE CREATED** |
| Replay runner label | `self-hosted,chonk,lean-eval-replay` | production | **TO BE PROVISIONED** |

Do not change a status to provisioned without replacing every applicable
placeholder in the inventory below and recording a verification date.

## Cloudflare account and zone

| Field | Recorded value |
| --- | --- |
| Account name | **TO BE RECORDED** |
| Account ID | **TO BE RECORDED AFTER PROVISIONING** |
| Zone | `lean-lang.org` |
| Zone ID | **TO BE RECORDED AFTER PROVISIONING** |
| Billing plan / cost owner | **TO BE RECORDED AFTER PROVISIONING** |
| Primary administrator | **TO BE RECORDED AFTER PROVISIONING** |
| Backup administrator | **TO BE RECORDED AFTER PROVISIONING** |

Worker configuration is declarative in [`server/wrangler.jsonc`](server/wrangler.jsonc):

- compatibility date `2026-08-20` with `nodejs_compat`;
- `workers.dev` and preview URLs disabled;
- full Workers observability enabled;
- distinct Worker names, custom domains, variables, credentials, and state
  repositories for staging and production;
- intake disabled by default and enabled only through a reviewed configuration
  change after the rollout gates pass.

There is no Terraform layer. Wrangler configuration plus this reviewed ledger
is the chosen infrastructure record. Resource identifiers created outside
Wrangler must be copied here immediately.

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
| `CLOUDFLARE_API_TOKEN` | secret | Workers Scripts edit for the one account and DNS/route access only as required for `eval-submit-staging.lean-lang.org` |

`cloudflare-production` contains the same names backed by a **different API
token**, restricted to the production Worker and route as narrowly as
Cloudflare permits. Neither token may administer unrelated zones or account
products. GitHub environment secrets are not exposed to pull-request checks.

Protected `main` is the human promotion decision. The production job has no
second manual approval, so an approved merge automatically reaches staging and
then production only after the smoke gate succeeds. Deployment workflow
concurrency is intentionally latest-main-wins: skipped intermediate commits
are already ancestors of the latest tested commit.

## Worker secrets and GitHub state access

Each Worker environment has a distinct Wrangler secret:

| Secret | Environment | Purpose | Minimum GitHub reach |
| --- | --- | --- | --- |
| `GITHUB_STATE_TOKEN` | staging | Atomically append staging events | `lean-eval-state-staging`, Contents write and Metadata read |
| `GITHUB_STATE_TOKEN` | production | Atomically append production events | `lean-eval-state`, Contents write and Metadata read |

Prefer separate GitHub Apps installations over personal access tokens. Record
the App slug, installation ID, credential owner, creation date, expiry, and last
rotation below when provisioned:

| Field | Staging | Production |
| --- | --- | --- |
| GitHub App slug | **TO BE RECORDED** | **TO BE RECORDED** |
| Installation ID | **TO BE RECORDED** | **TO BE RECORDED** |
| Credential owner | **TO BE RECORDED** | **TO BE RECORDED** |
| Created / expires | **TO BE RECORDED** | **TO BE RECORDED** |
| Last rotation drill | **TO BE RECORDED** | **TO BE RECORDED** |

The internet-facing Worker token must not write workflow files, modify
repository settings, reach `lean-eval-submissions`, or reach the other
environment's State. State writes use the Git Data API and a non-forced update
of `refs/heads/main`; repository administration is not needed at runtime.

## State repository controls

Both state repositories are private operational ledgers. Each immutable event
occupies exactly one file under `events/YYYY/MM/DD/`. Configure:

- default branch `main`;
- deletion and force-push protection;
- required pull request and status checks for human-authored changes;
- no broad Actions write token;
- a ruleset bypass limited to the environment-specific state-writer App;
- secret scanning and dependency alerts;
- scheduled validation of the whole event tree and an off-platform backup;
- audit-log review after credential rotation or unexplained ref contention.

Record ruleset IDs and backup destination after creation:

| Field | Staging | Production |
| --- | --- | --- |
| Branch ruleset ID | **TO BE RECORDED** | **TO BE RECORDED** |
| Backup destination | **TO BE RECORDED** | **TO BE RECORDED** |
| Restore drill date | **TO BE RECORDED** | **TO BE RECORDED** |

## Encrypted replay boundary

Submission source archives remain encrypted outside the evaluation job. Replay
is orchestrated through the existing chonk runner path, but plaintext is handled
only inside a fresh disposable VM carrying labels
`self-hosted,chonk,lean-eval-replay`. The orchestrator gives that VM a
single-submission decryption capability, never the archive master identity.
The VM is destroyed after the job and must not share a persistent workspace.

The key design is a launch gate. Before replay is enabled, add a reviewed threat
model that records the KMS or hardware-backed root, envelope format,
single-submission capability issuance and expiry, audit events, revocation,
operator recovery, and a successful restore/decrypt drill. Cloudflare Sandbox
SDK is not part of this design: replay runs trusted pipeline code around
untrusted Lean on existing hardened self-hosted infrastructure.

## Public releases

`leanprover/lean-eval-releases` owns public two-month-delayed source bundles,
checksums, provenance manifests, signatures, and release notes. It does not
receive Worker or State write credentials. Embargo expiry is two UTC calendar
months after acceptance, not a fixed day count. The release workflow must
recompute eligibility from State and refuse early publication.

The exact license wording and the interaction with contributor rights are a
separate legal/documentation review gate. No release job is enabled before that
text is approved.

## Monitoring and recovery

Minimum launch monitors:

- Worker exception and non-2xx rate split by environment;
- staging and production `/healthz` synthetic checks;
- `/readyz` alerting once intake is enabled;
- GitHub API rate-limit and ref-contention alerts;
- State tree validation and backup freshness;
- deployment failure notifications;
- replay VM creation, destruction, and capability-expiry audit events;
- delayed-release eligibility and publication failures.

Rollback changes only Worker code/configuration. It does not revert GitHub
State or other resources. To roll back code, identify the last known-good
deployment, run `npx wrangler rollback --env production`, smoke-test
`https://eval-submit.lean-lang.org/healthz`, and record the incident and version
IDs here. Never rewrite State to match an older Worker; deploy a compatibility
fix or append a corrective event.

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
2. compare GitHub environments, secret names, Apps, installations, permissions,
   repository visibility, rulesets, and runner labels to this file;
3. verify staging cannot reach production State and vice versa;
4. rotate one non-production credential and complete a staging deploy;
5. test rollback and State restore procedures;
6. update `Last reconciled`, owners, identifiers, dates, and drill results here.
