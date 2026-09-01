# Production launch readiness packet

Status: **NO-GO — lifecycle candidate not yet protected-qualified**

Scope authority: [LeanEval completion plan, section 7.5][completion-plan]

This is the compact production go/no-go packet. It records only the current
launch contract and the exact facts a maintainer must review before enabling a
capability. Update values in place; do not add a run history or evidence
appendix.

This lifecycle candidate follows the qualified all-false State-contract repair
without discarding the prior f09 bounded functional evidence. The rollback unit
is now live and coherent. The lifecycle candidate's protected checks, staging
deployment, immutable tag, and promotion canary must be supplied by a
subsequent packet update. Existing release-controller evidence must be read
back, not needlessly repeated.
Nothing in this document currently authorizes production enablement.

[completion-plan]: https://github.com/leanprover/lean-eval/blob/main/docs/overhaul-completion-plan.md#75-production-launch-readiness-packet

## 1. Exact binding

Protected `main` commits at the packet baseline:

| Repository | Commit |
| --- | --- |
| `leanprover/lean-eval` | `bcc165c27c6c546b27408454af35e3533e966463` |
| `leanprover/lean-eval-submissions` | `451856ebdd4ca4d875e43be7cd113678dea9e1b7` |
| `leanprover/lean-eval-leaderboard` | `b6df2533e2a6ceea8a6ed6eff5527cc3aef3e7c2` |
| `leanprover/lean-eval-state` | `235a96c96462438c7680e6fb90fa0e6044ec1774` |
| `leanprover/lean-eval-state-staging` | `6105a6255ec40409bcce66c6cf6b6764e0e93ed4` |
| `leanprover/lean-eval-releases` | `3c68d99f3de7060f7f0fdacf9340354775546c05` |
| `leanprover/lean-eval-generator` | `010b01634cccda2db538cf9b09e6f26ddc453743` |
| `leanprover/lean-eval-audit` | `f50c46574dd719486a01272e3eaeced396ac5ada` |

The prior f09 candidate and old 30bc rollback baseline are not a valid launch
unit after this repair. Production is fail-closed at the qualified replacement
baseline `451856ebdd4ca4d875e43be7cd113678dea9e1b7`. A subsequent packet must bind
the separate lifecycle candidate's fresh protected CI and exact-version staging
evidence before restoring `GO`. Intake-only PR `#1526` must then be recreated as
the same bounded seven-file semantic patch on the exact lifecycle merge rather
than replaying its old f09-based commits. Record the resulting exact values in
section 9; do not reuse superseded deployment bindings.

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
| Disabled baseline | Qualified at live commit `451856ebdd4ca4d875e43be7cd113678dea9e1b7`: production intake, general replay, historical-public replay, every public lifecycle family, model consolidation, and the promotion canary are effectively disabled. The baseline qualification used State `9cf3b4999bae2b6faaa32ff1bf5f040c5e6f787f`; protected production State is now its reviewed descendant `235a96c96462438c7680e6fb90fa0e6044ec1774`, and the release queue is empty. The public leaderboard retains stable problem pages and visible statements. |
| Archive boundary | Schema-version-3 archive-before-evaluation is deployed. Production archive authority is connected to the Encrypt-only Wrap role and has a qualified decrypt denial. The evaluation lane has no Wrap or unwrap authority. |
| Staging release boundary | Qualified: exact one-submission scope, consume-before-unwrap, identical reuse refusal, authority removal before reconstruction, source allowlisting, no plaintext artifact, no State/Git mutation, and cleanup. Publication and production authority remained absent. |
| Production release preflight | Qualified at release commit `3c68d99f3de7060f7f0fdacf9340354775546c05`: publication-disabled controller, State-write, audit-read, and exact ID-bearing `release-production` OIDC preflights pass. Re-read those bindings during the final packet audit; this Worker repair does not require repeating the campaign. No production archive may be decrypted or published while the packet is `NO-GO`. |
| Entry and submitter UI | The static `https://lean-lang.org/eval/submit/` entry page and the stable production application at `https://lean-eval-submission-server.lean-eval.workers.dev/` are live in disabled posture. The entry page states the policy and links to the Worker origin; the Worker supplies OAuth feedback, preserved form values, progress spinners, and status. One-way publication opt-in is kept separate from new intake on the dedicated `/release/` page when enabled. No LeanEval hostname or DNS change is required. |
| Exact-version staging | The prior f09 bounded browser, source-bound, owner/maintainer, denial, redaction, and reconstruction evidence remains valid. The replacement all-false baseline has fresh protected CI, exact-version staging and production readback, a promotion canary, and a successful no-op recovery proof. The lifecycle candidate still requires protected CI, exact-version staging readback, immutable tag, and promotion canary before `GO` can be restored. Repeat a bounded functional case only if those changes affect its path or the fresh checks expose drift. |

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

The launch-copy candidate is draft `leanprover/lean-eval` PR `#592` at exact
head `00b91e4f0943f0212d9184a951d3a08a1ab4244e`. Keep it draft until the
announcement timestamp and explicit target closure date replace every relative
date, and require all conditions below in its exact final diff before merge.

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

The fields below are a pending template. Bind the repair baseline and separate
lifecycle candidate without treating the prior functional checks as erased,
then change the top-level status to `GO` only after every gate is re-established.
The production-launch fields are the post-action finalization record and must
be filled immediately after each named production action.

```text
production release trust:
  protected release commit: 3c68d99f3de7060f7f0fdacf9340354775546c05
  repaired/read back at: 2026-09-01T15:11:16Z
  publication-disabled preflights:
    controller: https://github.com/leanprover/lean-eval-releases/actions/runs/33524122775
    audit read: https://github.com/leanprover/lean-eval-releases/actions/runs/33524126922
    OIDC trust: https://github.com/leanprover/lean-eval-releases/actions/runs/33524130789

final exact-version staging:
  replacement repair merge commit: 451856ebdd4ca4d875e43be7cd113678dea9e1b7
  replacement repair protected CI: https://github.com/leanprover/lean-eval-submissions/actions/runs/33535007875
  replacement repair deployment: https://github.com/leanprover/lean-eval-submissions/actions/runs/33535007713
  replacement repair production versions:
    intake: 7afc61bf-6427-431a-b4f6-c1c3ec2641ac
    broker: dfd77e4f-16ae-4a63-81ab-bbb79797385b
    replay: 570664e6-a6f5-428d-87cb-803dd5b1768f
  lifecycle candidate commit: <40-character protected-main SHA>
  immutable dispatch tag: <lean-eval-dispatch/full-SHA>
  protected checks and staging deployment: <successful Actions URLs>
  staging Worker version IDs: <intake, broker, replay>
  preserved bounded functional evidence:
    browser/headless: https://github.com/leanprover/lean-eval-submissions/actions/runs/33486313551
    submission IDs: 01a05c13-2269-747c-8b15-6a0eb5d95a76 / 01a05c13-ce49-7028-a69f-e072bbbcac83
    owner/maintainer/denial/opt-in: https://github.com/leanprover/lean-eval-submissions/actions/runs/33501400059
    publication-disabled reconstruction: https://github.com/leanprover/lean-eval-releases/actions/runs/33506645494
    leaderboard: https://github.com/leanprover/lean-eval-leaderboard/actions/runs/33506442829
  fresh all-false recovery: https://github.com/leanprover/lean-eval-submissions/actions/runs/33535723990
  final staging State and Results commits: d2d6f74f8d7b0a169689a2e9b4b5873709c2f827 / 06bfd1ed3f7a11db5cb33f5a581330077e55e80e

production launch:
  release controller enabled/read back at: <UTC timestamp and URL>
  lifecycle APIs enabled/read back at: <UTC timestamp>
  lifecycle deployment commit/run: <40-character SHA and Actions URL>
  intake-only protected staging promotion canary: <successful Actions URL>
  intake deployment commit/run: <40-character SHA and Actions URL>
  intake finite-lease activated/read back at: <UTC timestamp and successful Actions URL or compact result URL>
  intake durable transition/read back at: <UTC timestamp and successful Actions URL or compact result URL>
  production Worker version IDs: <intake, broker, replay>
  production canary source:
    repository: leanprover/lean-eval-state-staging
    ref: production-canary-source-fixture-v4
    commit: 18595c8d06ec29d47940065644d16ae7cfbd1591
  production canary problem: formalization-evaluation/substInv_X_sub_X_sq_eq_catalan@1
  production canary model: LeanEval production launch canary
  production canary publication sequence: private, then visible irreversible publication opt-in
  production canary submission/result: <UUIDv7 and terminal result URL>
  production State commit after validation: <40-character SHA>
  production health/readiness: <URL>
  repository announcement: <URL>
  announcement published at: <UTC timestamp>
  target issue-intake closure date: <YYYY-MM-DD>
```
