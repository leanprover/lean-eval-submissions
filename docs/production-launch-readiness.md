# Production launch readiness packet

Status: **GO — prelaunch gates complete; Phase 4 underway**

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
| `leanprover/lean-eval` | `cd6fc7c27fa5227b29b610558290c73994ffe84e` |
| `leanprover/lean-eval-submissions` | `ccd7a01a420d3c8dc18f996ea9efc65d38513b6d` |
| `leanprover/lean-eval-leaderboard` | `d7f0de9d9b5abbb62a4080df31002825a1afa814` |
| `leanprover/lean-eval-state` | Observed at `d699f057af46adee69aa4d1cbdd6966d7716abbb`; this append-only head may advance |
| `leanprover/lean-eval-state-staging` | `c604bb446a51fc833c96887053ec64672c912d8c` |
| `leanprover/lean-eval-releases` | `dbd9d7ca947e28b041fbb1b72667f0283265189f` |
| `leanprover/lean-eval-generator` | `010b01634cccda2db538cf9b09e6f26ddc453743` |
| `leanprover/lean-eval-audit` | `666950ce7702d1d2a1392b12f9104781ac9446e3` |

The protected submissions `staging-results` branch is
`1deb87414faf64edfa31639a8430fcf98fb2ccb5`.

Current production state and retained final-staging bindings:

| Boundary | Exact runtime and effective gates |
| --- | --- |
| Production submission unit | Commit `ccd7a01a420d3c8dc18f996ea9efc65d38513b6d`; intake is durable; the six approved lifecycle and one-way publication-opt-in gates are enabled with `kim-em` / GitHub user `477956` in both maintainer lists. Model consolidation, publication opt-out, and the promotion canary are disabled. |
| Production replay | Commit `ccd7a01a420d3c8dc18f996ea9efc65d38513b6d`; general replay, historical-public replay, and staging acceptance are disabled. |
| Final staging acceptance submission binding | Commit `f03f5cde4f1ac83b13ce78f294fc2273980dbf0a`; intake version `c55e2220-393a-4739-b0ad-71d8eb455dc2`, broker version `b93729b0-dfac-4fba-bf9b-12d318e2111f`; intake and every public lifecycle gate were false, with only the staging promotion canary enabled. |
| Final staging acceptance replay binding | Commit `f03f5cde4f1ac83b13ce78f294fc2273980dbf0a`; version `c91f96f2-a0f8-4900-a951-b8f26eaceef9`, container application version `22`; general and historical-public replay disabled; staging acceptance enabled. |
| Release controller | Protected commit `dbd9d7ca947e28b041fbb1b72667f0283265189f`; publication variable `PUBLICATION_ENABLED` is `true`. The production canary is scheduled for `2026-11-02T03:50:01.002Z`; its source is not yet due. |
| Public entry and leaderboard | Protected and deployed leaderboard `d7f0de9d9b5abbb62a4080df31002825a1afa814` retains stable lifecycle-aware problem pages and visible statements. `https://lean-lang.org/eval/submit/` remains issue-primary while server-primary presentation is pending. |

The immutable dispatch ref for the protected submissions candidate is
`lean-eval-dispatch/ccd7a01a420d3c8dc18f996ea9efc65d38513b6d`.
The automatic release controller, durable production intake, and the six
approved lifecycle and one-way publication-opt-in gates are enabled. Replay,
model consolidation, publication opt-out, and the promotion canary remain
disabled.

## 2. Launch gate status

The established prelaunch boundaries below are qualified. Held post-`GO`
candidates remain explicitly pending:

- schema-version-3 archive-before-evaluation with one per-submission envelope,
  strict submission/digest binding, production Encrypt-only Wrap authority,
  and no evaluation-lane wrap or unwrap authority;
- publication-disabled staging reconstruction with one-submission scope,
  consume-before-unwrap, reuse refusal, source allowlisting, no plaintext
  artifact, and no State mutation;
- production release OIDC, audit-read, State-write, and unwrap-invoker scope,
  with no production decrypt or publication performed during preflight;
- protected production State contract and event-schema readiness;
- exact-`f03f5cde` browser OAuth and source-bound headless archive, evaluation,
  and Result paths;
- launch-approved optional lifecycle routes, authorization denials, and
  redaction through repository tests and prior staging route evidence; an
  exact-`f03f5cde` route matrix is not a launch gate;
- one-way publication opt-in remains launch-enabled behind its feature flag;
  the production canary supplies its launch proof;
- production all-false recovery and coherent rollback; and
- lifecycle-aware leaderboard output with stable problem pages and visible
  statements.

The completed staging binding is recorded once in the finalization table
below. The packet has no remaining pre-production `GO` blocker.

The table records the completed staging binding and leaves future bindings
explicitly unfilled. `Pending` is not evidence that an action succeeded.

| Phase 4 finalization category | Required exact final binding | Current status |
| --- | --- | --- |
| Final staging smoke | Browser and source-bound headless paths; terminal archive, evaluation, Result, State, scheduled release, and all-false cleanup | **Complete:** the final staging acceptance binding above is retained; temporary staging fixture access is removed. |
| Automatic release | Protected releases `dbd9d7ca947e28b041fbb1b72667f0283265189f`; scoped credentials and write-free no-op controls; `PUBLICATION_ENABLED=true` | **Complete:** the enabled controller has one canary release scheduled for `2026-11-02T03:50:01.002Z`; no source is due now. |
| Production lifecycle APIs | Exact protected submissions commit and dispatch tag; effective-health readback showing only the approved lifecycle and publication-opt-in families enabled while intake, consolidation, opt-out, and replay remain false; one non-mutating authorization denial | **Complete:** the approved lifecycle and publication-opt-in surface is deployed at `ccd7a01a420d3c8dc18f996ea9efc65d38513b6d`. |
| Production intake lease and durable transition | Exact merged intake commit and dispatch tag; provisional-disabled and finite-lease version/readback; lease start and expiry; one-use smoke result; protected State head recheck; final durable intake, broker, and replay effective health | **Complete:** production intake is durable at `ccd7a01a420d3c8dc18f996ea9efc65d38513b6d`. |
| Production canary | Submission ID and packet-bound source/model identity; archive and evaluation terminal state; Result and State identity; initial withheld presentation; irreversible opt-in and scheduled presentation | **Complete:** submission `01a0603c-6189-7751-9c43-c904b50b477a` produced Result `r2_176e0f46710a69d54b3cbcc722a948b364de2acdf2a1ee6fe667f0a331254a59`; its one-way opt-in is scheduled for `2026-11-02T03:50:01.002Z`. Production State was observed at `d699f057af46adee69aa4d1cbdd6966d7716abbb` after these terminal events and may advance append-only. The fixture branch and temporary App repository access are removed. |
| Production pause | Exact pre-pause release, intake, broker, replay, and State heads; all-false recovery action and disabled readback; publication-disabled no-op preflight and unchanged State | **Complete:** the exact production all-false pause was exercised and verified. |
| Ordered restore | Separate release-controller, lifecycle-with-intake-disabled, and finite-lease-to-durable intake restore actions; exact commit/effective-health, publication posture, and protected-State readback after each action | **Complete:** release, lifecycle, and durable intake were restored in order at the reviewed production release. |
| Server-primary entry | Protected and deployed leaderboard commit/build/readback for the server-primary page with issue fallback; protected LeanEval launch-copy commit; verified live entry URL and security/license/release text | **Pending leaderboard `#91` and LeanEval `#603`; production service is restored and durable.** |
| Overlap announcement | Explicit UTC announcement time, UTC overlap start, target closure no earlier than four weeks later, issue-intake fallback URL, and subsequent closure-notice date at least two weeks before any closure | **Pending verified server-primary deployment; no dates are claimed yet.** |

## 3. Held launch presentation change

Production runtime launch is complete. The remaining held changes affect only
the server-primary entry and announcement copy.

| Draft | Exact candidate | Intended action |
| --- | --- | --- |
| [`lean-eval#603`](https://github.com/leanprover/lean-eval/pull/603) | Draft head `0a11245cf9ca680b4224bbdaeed85f8aae6b60a6` on protected base `cd6fc7c27fa5227b29b610558290c73994ffe84e` | Refresh the repository launch copy after the matching leaderboard entry is ready. |
| [`lean-eval-leaderboard#91`](https://github.com/leanprover/lean-eval-leaderboard/pull/91) | Draft head `e65e7e8081207b5564325afbcf96529d0e5bfc42` on protected base `d7f0de9d9b5abbb62a4080df31002825a1afa814` | Make server intake primary while retaining issue intake during the overlap. |

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

1. Before the production pause, bind the exact deployed intake, broker, replay,
   release, and protected State heads. Dispatch
   [`intake-disable-recovery.yml`](../.github/workflows/intake-disable-recovery.yml)
   at that protected submissions commit. Require intake, every public lifecycle
   family, and publication opt-in to read false; both maintainer arrays to read
   empty; consolidation and opt-out to remain false; and replay to remain dark.
2. Remove or set `PUBLICATION_ENABLED=false`, cancel any active controller run,
   and reconcile any committed `release.started` event. Run the exact protected
   write-free production no-op preflight and require unchanged production State,
   no archive or publication write, and effective disabled health. Block the
   `release-production` environment or revoke its scoped deploy keys/AWS trust
   only for an emergency stop.
3. Restore the reviewed settings as three separate actions in this exact order:
   release controller, lifecycle APIs with intake still disabled, then intake
   through its finite-lease-to-durable controller. After each action, re-read
   its exact deployed version, effective health, publication posture, and the
   unchanged or expected append-only protected State head. Do not use the
   earlier staging rollback as this production proof.
4. Use [`rollback-worker.yml`](../.github/workflows/rollback-worker.yml) only
   for a reviewed coherent intake/broker/replay target. It redeploys target
   code with current secrets and finishes with production intake disabled. The
   current coherent rollback unit is commit
   `ccd7a01a420d3c8dc18f996ea9efc65d38513b6d`, intake
   `1b1b12d1-2cf3-4f8f-8b32-ef064263d569`, replay
   `00501e8b-6285-4948-8386-2aa8ced3aea4`, and broker
   `24a74b99-c87f-4fba-a4ee-3d86cc59a0d2`.
5. Never rewrite State, Results, releases, audit objects, AWS data, credentials,
   or Git history as rollback.

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

Only the server-primary presentation and announcement remain. Keep the mutable
runbook current after each fact exists; do not prefill terminal values.

The single permitted production canary is bound before launch as follows:

| Field | Exact value |
| --- | --- |
| Submitter/owner | `kim-em` |
| Source repository | `leanprover/lean-eval-state-staging` |
| Source branch | `production-canary-source-fixture-v4` |
| Source commit | `3dc17f41ccd0c86fa443687fac784a42f3798183` |
| Problem group | `formalization-evaluation` |
| Problem ID | `substInv_X_sub_X_sq_eq_catalan` |
| Statement revision | `1` |
| Declared model | `LeanEval production launch canary 3dc17f41ccd0c86fa443687fac784a42f3798183` |
| Initial publication choice | `withheld` |
| Production metadata | `{}` |
| Result ID | `r2_176e0f46710a69d54b3cbcc722a948b364de2acdf2a1ee6fe667f0a331254a59` |
| Submission ID | `01a0603c-6189-7751-9c43-c904b50b477a` |
| Scheduled release | `2026-11-02T03:50:01.002Z` |

The canary is terminal, its one-way publication opt-in is scheduled, and its
fixture branch and temporary App repository access are removed.

1. **Make the server primary and begin overlap.** With restored intake publicly
   usable, merge and deploy a single-purpose leaderboard change that
   makes `https://lean-lang.org/eval/submit/` server-primary while preserving
   issue intake as the overlap fallback. Rebase, revalidate, and merge
   launch-copy draft `#603` against current protected LeanEval. Verify the live
   entry page before publishing the server URL, explicit UTC overlap start and
   target closure dates, issue fallback, and security/license/release contract.
   Zulip or comments on other repositories remain separately authorized
   external communication.

The packet is `GO`. Complete the remaining presentation action with pause or
rollback after any failed readback.
