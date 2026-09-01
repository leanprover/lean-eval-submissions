# Production launch readiness packet

Status: **NO-GO — final bounded staging smoke and all-false cleanup pending**

Scope authority: [LeanEval completion plan, section 7.5][completion-plan]

This is the compact launch-decision packet. It contains the current bindings,
controls, and remaining launch actions only. The mutable checklist belongs in
the [execution runbook][runbook]; neither document should accumulate run
histories or evidence appendices.

[completion-plan]: https://github.com/leanprover/lean-eval/blob/main/docs/overhaul-completion-plan.md#75-production-launch-readiness-packet
[runbook]: https://github.com/leanprover/lean-eval/blob/main/docs/overhaul-execution-runbook.md

## 1. Exact current bindings

Protected `main` at this packet revision:

| Repository | Commit |
| --- | --- |
| `leanprover/lean-eval` | `bcc165c27c6c546b27408454af35e3533e966463` |
| `leanprover/lean-eval-submissions` | `a4ed8f7b811cbf647f446a3ca872ff48436ab5e0` |
| `leanprover/lean-eval-leaderboard` | `b6df2533e2a6ceea8a6ed6eff5527cc3aef3e7c2` |
| `leanprover/lean-eval-state` | `235a96c96462438c7680e6fb90fa0e6044ec1774` |
| `leanprover/lean-eval-state-staging` | `42e6cab55fa31bf95d0bb0b5f3381aea433bd4dc` |
| `leanprover/lean-eval-releases` | `3c68d99f3de7060f7f0fdacf9340354775546c05` |
| `leanprover/lean-eval-generator` | `010b01634cccda2db538cf9b09e6f26ddc453743` |
| `leanprover/lean-eval-audit` | `2681d179d515b6843ee4a4f862d76983f09ea2e9` |

The protected submissions `staging-results` branch is
`06bfd1ed3f7a11db5cb33f5a581330077e55e80e`.

Current deployed state:

| Boundary | Exact runtime and effective gates |
| --- | --- |
| Production submission unit | Commit `451856ebdd4ca4d875e43be7cd113678dea9e1b7`; intake disabled; every public lifecycle family disabled; both maintainer lists empty; model consolidation, publication opt-out, and promotion canary disabled. Active Cloudflare versions: intake `7afc61bf-6427-431a-b4f6-c1c3ec2641ac`, broker `dfd77e4f-16ae-4a63-81ab-bbb79797385b`, replay `570664e6-a6f5-428d-87cb-803dd5b1768f`. |
| Production replay | Commit `451856ebdd4ca4d875e43be7cd113678dea9e1b7`; general replay, historical-public replay, and staging acceptance disabled. |
| Staging submission unit | Commit `a4ed8f7b811cbf647f446a3ca872ff48436ab5e0`; intake, lifecycle families, maintainer lists, publication opt-in, model consolidation, and publication opt-out currently read false after the bounded window. The staging-only dispatch promotion canary is intentionally true and is not a public lifecycle gate. Final cleanup remains pending until the corrected verifier proves every required false field. |
| Staging replay | Commit `a4ed8f7b811cbf647f446a3ca872ff48436ab5e0`; general and historical-public replay disabled; staging acceptance enabled. |
| Release controller | Commit `3c68d99f3de7060f7f0fdacf9340354775546c05`; production OIDC trust and publication-disabled preflights qualified; repository variable `PUBLICATION_ENABLED` absent. |
| Public entry and leaderboard | `https://lean-lang.org/eval/submit/` and `https://lean-eval-submission-server.lean-eval.workers.dev/` are live in disabled posture. Lifecycle-aware problem pages, including visible problem statements, are live. |

The immutable dispatch ref for the protected submissions candidate is
`lean-eval-dispatch/a4ed8f7b811cbf647f446a3ca872ff48436ab5e0`.
No current production capability is enabled by this packet.

## 2. Launch gate status

Everything below is qualified except the explicitly pending staging closeout:

- schema-version-3 archive-before-evaluation with one per-submission envelope,
  strict submission/digest binding, production Encrypt-only Wrap authority,
  and no evaluation-lane wrap or unwrap authority;
- publication-disabled staging reconstruction with one-submission scope,
  consume-before-unwrap, reuse refusal, source allowlisting, no plaintext
  artifact, and no State mutation;
- production release OIDC, audit-read, State-write, and unwrap-invoker scope,
  with no production decrypt or publication performed during preflight;
- protected production State contract and event-schema readiness;
- browser OAuth, source-bound headless intake, owner and maintainer lifecycle
  routes, one-way publication opt-in, authorization denials, redaction, and
  exact dispatch promotion in staging;
- production all-false recovery and coherent rollback; and
- lifecycle-aware leaderboard output with stable problem pages and visible
  statements.

The only pre-production item still open is terminal review of the bounded
browser smoke at the exact staging runtime above, followed by corrected
verification that every public launch gate is false. The staging-only
promotion canary remains at its reviewed true setting. A failed terminal
result, State inconsistency, cleanup failure, unexpected due-release work, or
non-coherent deployment keeps this packet `NO-GO`.

## 3. Draft launch changes — candidates, not facts

| Draft | Exact candidate | Intended action |
| --- | --- | --- |
| [`lean-eval-submissions#1603`](https://github.com/leanprover/lean-eval-submissions/pull/1603) | `1d3864193827e28ed50818910e092300fb47b413`, based on `a4ed8f7b811cbf647f446a3ca872ff48436ab5e0` | Change only tracked production intake from disabled to durable-enabled, using the protected deploy workflow's finite lease, smoke, and final durable transition. |
| [`lean-eval#603`](https://github.com/leanprover/lean-eval/pull/603) | `4ac6205ea122278db1101d4f5cd91cb4d28a954d`, based on `bcc165c27c6c546b27408454af35e3533e966463` | Make the server the primary submission path while retaining issue intake during the overlap. Merge only after server intake is public. |
| [`lean-eval#604`](https://github.com/leanprover/lean-eval/pull/604) | `b9a00afd1efe6c8299c5b23a30a5b1b1906b7e38`, based on `bcc165c27c6c546b27408454af35e3533e966463` | Refresh the mutable execution checklist for the final staging candidate and launch sequence. |

Opening or checking these drafts changes no runtime. Their commits, checks,
and stated effects must be re-read immediately before merge; a changed head is
a new candidate.

## 4. Credentials, ownership, and recovery

[`INFRASTRUCTURE.md`](../INFRASTRUCTURE.md) is the identifier and procedure
ledger. Kim Morrison is the temporary runtime-secret and OAuth custodian unless
organization ownership supplies recovery.

| Credential class | Scope and owner | Rotation, revocation, and recovery |
| --- | --- | --- |
| Cloudflare deploy token | One dedicated `lean-eval` account; Workers Scripts and Containers edit only; separate staging and production copies; no DNS or zone permission. Kim Morrison is custodian. | Replace one environment secret, verify its intended state, then revoke the old token. |
| Worker readiness, callback, and session secrets | `READINESS_TOKEN` and `LIFECYCLE_CALLBACK_TOKEN` are shared only by one Worker and its matching protected environment; `AUTH_TOKEN_SECRET` signs only that environment's sessions. | Rotate each Worker/environment pair independently. Overwrite both copies to revoke shared tokens. Replacing the auth secret intentionally invalidates that environment's sessions. Keep intake disabled until replacement verification succeeds. |
| Production State writer | Contents write only on `leanprover/lean-eval-state`; expires `2026-11-19`; Kim Morrison is custodian. | Install and verify an equally scoped replacement by `2026-11-05`, then revoke the old token. Loss or rate exhaustion fails readiness and intake closed. |
| GitHub Apps | Organization-owned Source Reader App `4666604` has metadata/contents read only on opted-in source repositories. Workflow Dispatcher App `4666633` has metadata/contents/actions only on `leanprover/lean-eval-submissions`. Kim Morrison temporarily holds private keys. | Create and verify one replacement key before deleting the old key. Immediate revocation deletes the App key and both broker secrets; organization ownership is the recovery path. Neither App has Gist or broad organization authority. |
| Release deploy keys | Separate audit-read, production-State-write, and releases-write keys. | Rotate and verify one key at a time. Immediate revocation deletes its public deploy key before removing the environment secret. |
| AWS archive and release sessions | GitHub OIDC only in account `161072922960`: archive may assume only `lean-eval-archive-wrap-production`; release may invoke only `lean-eval-archive-unwrap-production:live`. No long-lived AWS key. | Revoke by removing the exact environment role variable or narrowing/removing its OIDC trust. Removing the publication latch and cancelling controller runs pauses release independently. |

The temporary production OAuth App is owned by `kim-em`: App ID `3806359`,
client ID `Ov23liFcOLHsyvY9DmQ5`, callback
`https://lean-eval-submission-server.lean-eval.workers.dev/api/v1/oauth/callback`,
scope `read:user`, device flow disabled, expiring user tokens enabled. Rotate by
installing and verifying a new client secret in the production Worker before
revoking the old secret. If the account cannot be recovered, pause browser
intake; organization transfer is not an initial-launch gate.

## 5. Submitter-facing contract

The [entry page](https://lean-lang.org/eval/submit/) and production application
must state that:

- the submitter must control the exact source and must not submit secrets or
  material they cannot disclose;
- LeanEval archives and evaluates source privately, but confidentiality is
  best-effort rather than guaranteed;
- evaluation metadata and results are public;
- `scheduled` confirms authority to release accepted source under Apache-2.0
  exactly two UTC calendar months after acceptance;
- `withheld` keeps accepted source private while retaining its public result;
  the owner may later make the irreversible transition to `scheduled`; and
- there is no available scheduled-to-private opt-out route or control, and no
  user-facing documentation may promise one.

## 6. Pause and rollback

1. Dispatch
   [`intake-disable-recovery.yml`](../.github/workflows/intake-disable-recovery.yml)
   to force intake and every public lifecycle gate false, clear both maintainer
   arrays, and verify exact effective health.
2. Use [`rollback-worker.yml`](../.github/workflows/rollback-worker.yml) only
   for a reviewed coherent intake/broker/replay target. It redeploys target
   code with current secrets and finishes with production intake disabled.
3. Pause publication by removing or setting `PUBLICATION_ENABLED=false`,
   cancelling active controller runs, and reconciling any committed
   `release.started` event. Block `release-production` or revoke its scoped
   deploy keys/AWS trust for an emergency stop.
4. Never rewrite State, Results, releases, audit objects, AWS data, credentials,
   or Git history as rollback. After recovery, verify exact `/healthz`,
   authenticated `POST /readyz`, protected State, and independent publication
   posture before resuming.

## 7. Deferred or deliberately disabled

- Historical replay and legacy private-envelope migration continue after
  launch; general and historical-public replay remain disabled meanwhile.
- Model consolidation remains disabled. Publication opt-out remains absent.
- FC integration, disproof support, experimental kernels, persistent
  qualification machinery, model-consolidation launch, automatic copycat
  detection, a second key provider, and verified-calculation runner
  infrastructure are outside this overhaul.
- The neutral open-problems tab may remain empty.
- Issue intake remains available for at least the announced four-week overlap
  and closes only after the separate incident, adoption, notice, and final-data
  gates pass.

## 8. Remaining launch actions

Only these actions are pending, in order. Record their actual commits, runtime
versions, State heads, timestamps, and URLs in the mutable runbook after they
exist; do not prefill terminal values here.

1. **Close final staging.** Verify the bounded browser smoke's terminal
   archive/evaluation/Result/State/release-readiness and leaderboard behavior.
   Restore intake, lifecycle, maintainer, and publication-opt-in gates to false
   through the reviewed disable path, while retaining the staging-only
   promotion canary at its reviewed true setting. Use the corrected verifier,
   then re-read staging State, Results, and public health. Reconcile and merge
   runbook draft `#604` only after those facts are known.
2. **Enable automatic release.** Confirm no release is due, enable the
   publication controller at the exact protected release commit, and read back
   its State, audit, OIDC, and publication posture.
3. **Deploy production lifecycle APIs.** Reconfirm production is all-false and
   no newer protected deployment supersedes `a4ed8f7b811cbf647f446a3ca872ff48436ab5e0`.
   Complete its held production deployment, leaving intake disabled while
   enabling only the launch-approved owner, maintainer, alias/rename, and
   one-way publication-opt-in families. Keep consolidation and opt-out false.
4. **Enable server intake.** Revalidate draft `#1603`, merge its exact reviewed
   head or a newly reviewed descendant, and require the deployment to pass the
   provisional-disabled checks, finite lease, one-use smoke, protected-State
   recheck, and final durable transition.
5. **Run one production canary.** Submit the packet-bound private canary,
   verify archive-before-evaluation, terminal State and Result, leaderboard
   presentation, and release readiness, then exercise the visible irreversible
   private-to-scheduled opt-in. On any failure, run all-false recovery and
   pause publication.
6. **Publish launch and begin overlap.** Revalidate and merge launch-copy draft
   `#603` only after intake is publicly usable. Publish the server URL, explicit
   UTC overlap start and target closure dates, the issue-intake fallback, and
   the security/license/release contract above. Zulip or comments on other
   repositories remain separately authorized external communication.

The packet becomes `GO` only after action 1 succeeds and its all-false cleanup
is verified. Actions 2–6 then proceed under that `GO`, one capability at a
time, with pause or rollback after any failed readback.
