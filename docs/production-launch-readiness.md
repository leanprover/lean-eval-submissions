# Production launch readiness packet

Status: **NO-GO — final staging and launch-packet refresh are pending**

Scope authority: [LeanEval completion plan, section 7.5][completion-plan]

This is the compact production go/no-go packet. It records only the current
launch contract and the exact facts a maintainer must review before enabling a
capability. Update values in place; do not add a run history or evidence
appendix.

[completion-plan]: https://github.com/leanprover/lean-eval/blob/main/docs/overhaul-completion-plan.md#75-production-launch-readiness-packet

## 1. Exact binding

Protected `main` commits at the packet baseline:

| Repository | Commit |
| --- | --- |
| `leanprover/lean-eval` | `a0a06faa95f2ee15578675c6dacc596a83b17db3` |
| `leanprover/lean-eval-submissions` | `f09e30565ec8f180cb7b0a85935f9439f802a14c` |
| `leanprover/lean-eval-leaderboard` | `61a7193b94b027049b8e912e7ca8c9070424004f` |
| `leanprover/lean-eval-state` | `9cf3b4999bae2b6faaa32ff1bf5f040c5e6f787f` |
| `leanprover/lean-eval-state-staging` | `a15552ad0fa4c13df1fa3ed7d9d83b8e8d4fba2d` |
| `leanprover/lean-eval-releases` | `a8ad23df2cf69671bd4406ae74b10c009a3daa64` |
| `leanprover/lean-eval-generator` | `010b01634cccda2db538cf9b09e6f26ddc453743` |
| `leanprover/lean-eval-audit` | `521269726459b58c1e44039b6d51b550048b59d6` |

Final staging is exercising the submissions commit above through immutable tag
`lean-eval-dispatch/f09e30565ec8f180cb7b0a85935f9439f802a14c`. The protected
launch workflow must deploy that same exact commit before intake is enabled.
The current production Worker baseline is
`30bc92b3d46bd2a3ba1788433264fdd70ae3c74e`, with every launch capability
disabled. Its active intake, broker, and replay Worker versions are
`f6f7cd06-306a-41ee-a26c-fe8fcddbfd8c`,
`4ff853f6-892e-4ac8-91eb-b379a3f604e2`, and
`003a9c98-8bab-4e19-bfde-0fa0f9ed695e`, respectively. Record the exact final
staging and launch versions in section 9.

## 2. Capability decision

The launch sequence consists of separately visible actions. Capability gates
have explicit pause or rollback paths; the canary's publication opt-in is
irreversible, and publishing the announcement starts the overlap. Standing
maintainer authorization covers the sequence after every gate in this packet
reads `GO`.

| Capability | Current production state | Launch state |
| --- | --- | --- |
| Automatic release controller | `PUBLICATION_ENABLED` absent | `true`, initially with an empty release queue |
| Result-owner APIs | disabled | legacy-result claim prerequisite, metadata backfill, and repair/retraction enabled |
| Maintainer APIs | disabled, empty lists | decisions enabled for exactly `kim-em` / GitHub user `477956` |
| Model identity APIs | identity-creation prerequisite and alias/rename disabled; consolidation disabled | identity creation and alias/rename enabled; consolidation remains disabled |
| Publication opt-in | disabled | one-way private-to-scheduled transition enabled; reverse transition disabled |
| Server intake | disabled, durable lease absent | enabled through the finite-lease controller, then durable |
| Production canary | none | one packet-bound, withheld-source canary after intake; verify archive, evaluation, State, Result, leaderboard, and scheduling |
| General and historical replay | disabled | remain disabled; bounded historical workflows are separate |

The decisions are not bundled: enable the release controller, lifecycle APIs,
intake, run the production canary, and publish the repository announcement in
that order, verifying each readback before proceeding.

The enabled surface includes two prerequisite routes rather than hiding them
behind feature labels: `POST /api/v1/results/claims` establishes immutable
owner authority before historical metadata backfill, and `POST
/api/v1/model-identities` creates the owner-bound identity required before an
alias or rename. They share the reviewed owner gates and do not enable intake,
model consolidation, replay, or publication.

## 3. Gates from completion-plan sections 7.2–7.4

| Gate | Current result |
| --- | --- |
| Disabled baseline | Production intake, general replay, historical-public replay, every public lifecycle family, model consolidation, and the promotion canary are effectively disabled. Protected production State validates at `9cf3b4999bae2b6faaa32ff1bf5f040c5e6f787f`; the deployed lifecycle contract pin remains its validated ancestor `c6a4bb67b55609ae7215bdd3cac2378b2db42a0a`. The release queue is empty. The public leaderboard retains stable problem pages and visible statements. |
| Archive boundary | Schema-version-3 archive-before-evaluation is deployed. Production archive authority is connected to the Encrypt-only Wrap role and has a qualified decrypt denial. The evaluation lane has no Wrap or unwrap authority. |
| Staging release boundary | Qualified: exact one-submission scope, consume-before-unwrap, identical reuse refusal, authority removal before reconstruction, source allowlisting, no plaintext artifact, no State/Git mutation, and cleanup. Publication and production authority remained absent. |
| Production release preflight | Qualified at release commit `a02e06e7ce5258cdde23b6dee79666355b947a21`, with publication absent and no due work. The controller State-write, audit-read, and exact ID-bearing `release-production` OIDC trust preflights all pass. No production archive was decrypted or published. |
| Entry and submitter UI | The static `https://lean-lang.org/eval/submit/` entry page and the stable production application at `https://lean-eval-submission-server.lean-eval.workers.dev/` are live in disabled posture. The entry page states the policy and links to the Worker origin; the Worker supplies OAuth feedback, preserved form values, progress spinners, and status. One-way publication opt-in is kept separate from new intake on the dedicated `/release/` page when enabled. No LeanEval hostname or DNS change is required. |
| Exact-version staging | Browser submission `01a05c13-2269-747c-8b15-6a0eb5d95a76` and source-bound submission `01a05c13-ce49-7028-a69f-e072bbbcac83` passed archive, evaluation, Result, and State validation on exact commit `f09e30565ec8f180cb7b0a85935f9439f802a14c`. **PENDING:** finish the bounded owner-lifecycle cases, publication-disabled reconstruction, redaction checks, all-false rollback, and final State validation recorded in section 9. |

Any non-coherent Worker deployment, nonempty due-release queue, failed final
staging case, or unexpected State event changes this packet to `NO-GO`.

## 4. Credentials and ownership

The exact identifiers, environments, dates, and procedures are maintained in
[`INFRASTRUCTURE.md`](../INFRASTRUCTURE.md). Kim Morrison is the temporary
custodian except where organization ownership provides recovery.

| Credential class | Scope | Rotation and revocation |
| --- | --- | --- |
| Production Cloudflare deploy token | Dedicated `lean-eval` account; Workers Scripts and Containers edit only; no zone/DNS permission | Created `2026-08-21`, no expiry. Replace only in `cloudflare-production`, verify, then revoke the old token. |
| Worker `READINESS_TOKEN` and `LIFECYCLE_CALLBACK_TOKEN` | Matching production Worker endpoint and protected GitHub environment only | No application expiry. Replace each Worker/environment pair as one unit; overwrite both copies to revoke. |
| Worker `AUTH_TOKEN_SECRET` and OAuth client secret | Production session signing and the production personal OAuth App only | Replace only the production Worker value and verify fresh sessions/OAuth before revoking the old value. An auth-secret change intentionally invalidates existing sessions. |
| Production State writer | Contents write on `leanprover/lean-eval-state` only | Expires `2026-11-19`; rotate by `2026-11-05`. Install and verify an equally scoped replacement, then revoke the old token. |
| Source-reader and workflow-dispatch App keys | Metadata/contents read on opted-in source repos; metadata/contents/actions on submissions only | No recorded expiry. Create one replacement App key, install and verify it, then delete the old key. App ownership by `leanprover` is the recovery path. Neither App has Gist or broad organization authority. |
| Release deploy keys | Audit read, production State write, and releases write are three distinct keys | No provider expiry. Replace and verify one key at a time, then delete its old public key. Immediate revocation deletes the public key first and removes its environment secret. |
| AWS archive and release sessions | GitHub OIDC only: production archive may assume the Encrypt-only Wrap role; production release may invoke only the versioned unwrap Lambda | No long-lived AWS access key. Revoke by removing the exact environment role variable or narrowing/removing its OIDC trust. Release pause also removes the publication latch and cancels active runs. |

Production archive authority is connected and its decrypt denial is qualified.
Production release authority is connected to the exact ID-bearing OIDC subject
and its publication-disabled preflights pass. Automatic publication remains
disabled because the separate publication latch is absent.

Browser OAuth is temporarily owned by personal account `kim-em`, which is an
accepted launch limitation. Production client ID
`Ov23liFcOLHsyvY9DmQ5` has the exact callback
`https://lean-eval-submission-server.lean-eval.workers.dev/api/v1/oauth/callback`,
requests only `read:user`, disables device flow, and uses expiring user tokens.
Kim Morrison owns rotation and account recovery; if that account is
unavailable, pause browser intake. Later transfer to `leanprover` is not a
launch gate.

## 5. Submitter-facing contract

The authoritative text is on the [submission entry page][entry], the stable
[production application][application], and in the [Worker form
implementation][worker-form]. It tells submitters that:

- they must have authority to provide the exact source and must not submit
  secrets or material they cannot disclose;
- LeanEval stores and runs source privately for evaluation, but confidentiality
  is best-effort rather than guaranteed;
- evaluation metadata and results become public;
- `scheduled` is the default publication choice and confirms authority to
  license accepted source under Apache License 2.0 exactly two UTC calendar
  months after acceptance; and
- `withheld` keeps accepted source private while leaving the public result;
  the submitter may later schedule release, and a scheduled choice cannot be
  changed back to private.

[entry]: https://lean-lang.org/eval/submit/
[application]: https://lean-eval-submission-server.lean-eval.workers.dev/
[worker-form]: https://github.com/leanprover/lean-eval-submissions/blob/main/server/src/browser-ui.ts

## 6. Rollback and emergency pause

1. For intake or lifecycle failure, dispatch
   [`intake-disable-recovery.yml`](../.github/workflows/intake-disable-recovery.yml).
   It deploys only the all-false configuration, clears both maintainer arrays,
   and verifies the exact recovered version and effective public health.
2. For a coherent code rollback, use
   [`rollback-worker.yml`](../.github/workflows/rollback-worker.yml) with one
   reviewed target commit and its broker, replay, and intake version IDs. Keep
   intake disabled throughout.
3. For publication pause, remove or set `PUBLICATION_ENABLED=false`, cancel
   every queued/running controller run, and reconcile any committed
   `release.started` event. For an emergency stop, also block
   `release-production` or revoke its scoped release keys/AWS role.
4. A rollback never rewrites State, Results, releases, audit objects, AWS
   resources, credentials, or Git history. Forward-deploy or rerun recovery to
   restore one coherent component set.
5. After recovery or rollback, read back `/healthz` and make the authenticated
   `POST /readyz` State-writer proof: intake, broker, and general replay must be
   false, the recovered Worker version must match the recorded deployment, and
   State validation must still pass. If publication was paused, also read back
   its independent gate and verify that production publication cannot proceed
   before investigation continues.

## 7. Deferred functions and launch limitations

- Historical corpus replay and legacy private-envelope migration continue
  after launch and do not block new schema-version-3 submissions.
- GitHub issue intake remains available for the four-week overlap.
- Model consolidation stays disabled. FC integration, disproof support,
  experimental kernels, persistent qualification machinery, automatic copycat
  detection, a second key provider, and verified-calculation runner
  infrastructure are outside this overhaul.
- The neutral open-problems tab may be empty.
- Browser OAuth and several credential sets have one temporary human custodian;
  the recorded pause/recovery paths are the launch mitigation.
- The production State writer is a user-scoped GitHub token whose API allowance
  shares its issuer's rate bucket. Exhaustion makes readiness and intake fail
  closed until capacity returns; avoid unrelated high-volume authenticated API
  work from that principal whenever the server is in operational use. A later
  dedicated GitHub App installation-token path would remove this coupling, but
  is an availability improvement rather than a launch gate under the accepted
  completion plan.

## 8. Repository announcement

Publish only after the first four production actions have succeeded. The
overlap starts at the recorded announcement-publication timestamp, not at an
earlier capability-enablement time. Before publishing, calculate and record
the explicit UTC calendar closure date 28 days after that timestamp; do not
publish a relative-date placeholder. Repository publication is covered by
standing authorization; a Zulip post still requires separate exact approval.

The announcement must include all of the following reviewed facts:

- the static leaderboard entry URL,
  **https://lean-lang.org/eval/submit/**, and the stable lifecycle-aware
  submission application URL,
  **https://lean-eval-submission-server.lean-eval.workers.dev/**;
- the explicit target issue-intake closure date and the condition that issue
  intake closes only after the incident, adoption, overlap, and two-week notice
  gates pass;
- authenticated exact-ref intake, initially private evaluation-group source,
  public evaluation metadata and results, and best-effort confidentiality;
- the `scheduled` Apache-2.0 release policy, exactly two UTC calendar months
  after acceptance, the `withheld` private-source choice, and the irreversible
  later transition from private to scheduled;
- lifecycle amendments, model aliases, publication choice, and status; and
- a request to report server problems by
  [opening a submissions issue](https://github.com/leanprover/lean-eval-submissions/issues/new),
  with any revised overlap date announced explicitly.

## 9. Finalization record

These are the only facts intentionally left open. Fill them after the named
operation, then change the top-level status to `GO` only if every gate above is
still true.

```text
production release trust:
  repaired/read back at: 2026-09-01T08:31:49Z
  publication-disabled preflights:
    controller: https://github.com/leanprover/lean-eval-releases/actions/runs/33487460923
    audit read: https://github.com/leanprover/lean-eval-releases/actions/runs/33487462999
    OIDC trust: https://github.com/leanprover/lean-eval-releases/actions/runs/33487464866

final exact-version staging:
  submissions candidate commit: f09e30565ec8f180cb7b0a85935f9439f802a14c
  immutable dispatch tag: lean-eval-dispatch/f09e30565ec8f180cb7b0a85935f9439f802a14c
  base staging deployment job: https://github.com/leanprover/lean-eval-submissions/actions/runs/33485886104/job/99786227452
  base staging promotion-canary job: https://github.com/leanprover/lean-eval-submissions/actions/runs/33485886104/job/99786472062
  base staging Worker version IDs:
    intake: 7d56a757-11a5-48ad-bd33-37aab2c1a837
    broker: 6cd807ea-1b60-4f27-8567-167dd21289a5
    replay: 438d5e80-faa2-470f-8a97-5f0600e54aa8
  browser/headless acceptance intake version: 663ede5c-de99-4bff-8ff6-8d2ffd576824
  browser/headless activation job: https://github.com/leanprover/lean-eval-submissions/actions/runs/33486313551/job/99787601214
  browser submission ID: 01a05c13-2269-747c-8b15-6a0eb5d95a76
  source-bound submission ID: 01a05c13-ce49-7028-a69f-e072bbbcac83
  owner-lifecycle target intake version: b5f6f909-2e94-481f-892a-9d73c84be050
  owner-lifecycle target activation job: https://github.com/leanprover/lean-eval-submissions/actions/runs/33492047402/job/99805548900
  owner-lifecycle target submission/result: 01a05c49-8061-732c-a838-ec9e0a75cfb2 / <Result ID>
  final lifecycle-enabled intake version/run: <version ID and successful Actions URL>
  final all-false intake version/run: <version ID and successful Actions URL>
  staging State commit after validation: <40-character SHA>
  publication-disabled reconstruction: <successful Actions URL>
  bounded lifecycle result: <successful Actions URL or compact result URL>
  all-false rollback/readback: <successful Actions URL or compact result URL>

production launch:
  enabled at: <UTC timestamp>
  release-controller change/readback: <URL>
  lifecycle deployment commit/run: <40-character SHA and Actions URL>
  intake-only protected staging promotion canary: <successful Actions URL>
  intake deployment commit/run: <40-character SHA and Actions URL>
  intake finite-lease activation/readback: <successful Actions URL or compact result URL>
  intake durable transition/readback: <successful Actions URL or compact result URL>
  production Worker version IDs: <intake, broker, replay>
  production canary source:
    repository: leanprover/lean-eval-state-staging
    ref: production-canary-source-fixture-v4
    commit: 18595c8d06ec29d47940065644d16ae7cfbd1591
  production canary problem: formalization-evaluation/substInv_X_sub_X_sq_eq_catalan@1
  production canary model: LeanEval production launch canary 18595c8d06ec29d47940065644d16ae7cfbd1591
  production canary publication sequence: private, then visible irreversible publication opt-in
  production canary submission/result: <UUIDv7 and terminal result URL>
  production State commit after validation: <40-character SHA>
  production health/readiness: <URL>
  repository announcement: <URL>
  announcement published at: <UTC timestamp>
  target issue-intake closure date: <YYYY-MM-DD>
```
