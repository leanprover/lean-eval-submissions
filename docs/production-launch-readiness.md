# Production launch readiness packet

Status: **NO-GO — final staging and production release trust are pending**

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
| `leanprover/lean-eval` | `9625d5f290ca70c293ed73e8ea91fd40e61480e2` |
| `leanprover/lean-eval-submissions` | `81e94fe2f4fc819300fd7d4e036f00124166784f` |
| `leanprover/lean-eval-leaderboard` | `bf534c149e204a286a5cd9bbaff449449567834b` |
| `leanprover/lean-eval-state` | `c6a4bb67b55609ae7215bdd3cac2378b2db42a0a` |
| `leanprover/lean-eval-state-staging` | `ee990a23e9ba29b7fbb7b03b0fe5b0f490c796aa` |
| `leanprover/lean-eval-releases` | `071a52e2095d47ae4684ee983a7e08520f3c808a` |
| `leanprover/lean-eval-generator` | `010b01634cccda2db538cf9b09e6f26ddc453743` |
| `leanprover/lean-eval-audit` | `eadf24b2b4a99c56ef59a43811eab9d54ae013ac` |

The protected launch workflow must replace the submissions binding above with
the exact merged capability configuration it deploys, and final staging must
have exercised that same commit and its immutable
`lean-eval-dispatch/<commit>` tag. The current production Worker baseline is
`c1013bee0b5b2f57956501e0258d27dc30413d2b`, with every launch capability
disabled. Record the exact final staging and launch versions in section 9.

## 2. Capability decision

The launch sequence consists of five separately visible, reversible actions.
Standing maintainer authorization covers them after every gate in this packet
reads `GO`.

| Capability | Current production state | Launch state |
| --- | --- | --- |
| Automatic release controller | `PUBLICATION_ENABLED` absent | `true`, initially with an empty release queue |
| Result-owner APIs | disabled | metadata backfill and repair/retraction enabled |
| Maintainer APIs | disabled, empty lists | decisions enabled for exactly `kim-em` / GitHub user `477956` |
| Model identity APIs | alias/rename disabled; consolidation disabled | alias/rename enabled; consolidation remains disabled |
| Release opt-out | disabled | enabled |
| Server intake | disabled, durable lease absent | enabled through the finite-lease controller, then durable |
| Production canary | none | one packet-bound, withheld-source canary after intake; verify archive, evaluation, State, Result, leaderboard, and scheduling |
| General and historical replay | disabled | remain disabled; bounded historical workflows are separate |

The decisions are not bundled: enable the release controller, lifecycle APIs,
intake, run the production canary, and publish the repository announcement in
that order, verifying each readback before proceeding.

## 3. Gates from completion-plan sections 7.2–7.4

| Gate | Current result |
| --- | --- |
| Disabled baseline | Production intake, general replay, historical-public replay, every public lifecycle family, model consolidation, and the promotion canary are effectively disabled. Production State validates at `c6a4bb67b55609ae7215bdd3cac2378b2db42a0a` with 469 immutable events and an empty release queue. The public leaderboard retains stable problem pages and visible statements. |
| Archive boundary | Schema-version-3 archive-before-evaluation is deployed. Production archive authority is connected to the Encrypt-only Wrap role and has a qualified decrypt denial. The evaluation lane has no Wrap or unwrap authority. |
| Staging release boundary | Qualified: exact one-submission scope, consume-before-unwrap, identical reuse refusal, authority removal before reconstruction, source allowlisting, no plaintext artifact, no State/Git mutation, and cleanup. Publication and production authority remained absent. |
| Production release preflight | **PENDING:** repair the exact ID-bearing `release-production` OIDC trust, read it back, and pass the publication-disabled trust-only preflight. Do not decrypt or publish a production archive. |
| Entry and submitter UI | `https://lean-lang.org/eval/submit/` and the production Worker application are live in disabled posture. The entry page states the policy and links to the Worker origin; the Worker supplies OAuth feedback, preserved form values, progress spinners, status, and visible opt-out UI when enabled. |
| Exact-version staging | **PENDING:** record the successful browser and source-bound submissions, denial cases, archive/Result/State/scheduling checks, publication-disabled reconstruction, redaction checks, all-false rollback, and validated final staging State in section 9. |

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
Production release authority remains unusable until the pending OIDC trust
repair in section 3 succeeds.

Browser OAuth is temporarily owned by personal account `kim-em`, which is an
accepted launch limitation. Production client ID
`Ov23liFcOLHsyvY9DmQ5` has the exact callback
`https://lean-eval-submission-server.lean-eval.workers.dev/api/v1/oauth/callback`,
requests only `read:user`, disables device flow, and uses expiring user tokens.
Kim Morrison owns rotation and account recovery; if that account is
unavailable, pause browser intake. Later transfer to `leanprover` is not a
launch gate.

## 5. Submitter-facing contract

The authoritative text is on the [submission entry page][entry] and in the
[Worker form implementation][worker-form]. It tells submitters that:

- they must have authority to provide the exact source and must not submit
  secrets or material they cannot disclose;
- LeanEval stores and runs source privately for evaluation, but confidentiality
  is best-effort rather than guaranteed;
- evaluation metadata and results become public;
- `scheduled` is the default publication choice and confirms authority to
  license accepted source under Apache License 2.0 exactly two UTC calendar
  months after acceptance; and
- `withheld`, including the visible pre-release opt-out, prevents automatic
  source publication while leaving the public result.

[entry]: https://lean-lang.org/eval/submit/
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

Publish this only after the first four production actions have succeeded. The overlap
starts at the recorded enablement timestamp and its target end is 28 days
later. Repository publication is covered by standing authorization; a Zulip
post still requires separate exact approval.

> Lean Eval's lifecycle-aware submission server is now available at
> **https://lean-lang.org/eval/submit/**. GitHub-issue intake remains available
> during an initially planned four-week transition, with a target closure date
> of **<28 days after the section 9 launch timestamp>**.
>
> Server-path submissions use authenticated, exact-ref intake. Evaluation-group
> source starts private. When the publication choice is `scheduled`, accepted
> source is released under the Apache License 2.0 exactly two UTC calendar
> months after acceptance; choosing `withheld` opts out. Lifecycle amendments,
> model aliases, publication choice, and status are available through the new
> path. Issue submissions keep their existing policy during the transition.
>
> Please report server problems at
> **https://github.com/leanprover/lean-eval-submissions/issues/1310**. Issue
> intake will close only after the incident, adoption, four-week overlap, and
> two-week notice gates pass; any revised date will be announced explicitly.

## 9. Finalization record

These are the only facts intentionally left open. Fill them after the named
operation, then change the top-level status to `GO` only if every gate above is
still true.

```text
production release trust:
  repaired/read back at: <UTC timestamp>
  publication-disabled preflight: <successful Actions URL>

final exact-version staging:
  submissions candidate commit: <40-character SHA>
  immutable dispatch tag: lean-eval-dispatch/<same SHA>
  browser submission ID: <UUIDv7>
  source-bound submission ID: <UUIDv7>
  staging State commit after validation: <40-character SHA>
  publication-disabled reconstruction: <successful Actions URL>
  bounded lifecycle result: <successful Actions URL or compact result URL>
  all-false rollback/readback: <successful Actions URL or compact result URL>

production launch:
  enabled at: <UTC timestamp>
  release-controller change/readback: <URL>
  lifecycle deployment commit/run: <40-character SHA and Actions URL>
  intake deployment commit/run: <40-character SHA and Actions URL>
  production Worker version IDs: <intake, broker, replay>
  production canary source/problem: <exact nonsecret binding>
  production canary submission/result: <UUIDv7 and terminal result URL>
  production State commit after validation: <40-character SHA>
  production health/readiness: <URL>
  repository announcement: <URL>
  target issue-intake closure date: <YYYY-MM-DD>
```
