# Production launch readiness packet

Status: **production launch complete; overlap active**

Scope authority: [LeanEval completion plan, section 7.5][completion-plan]

This is the compact launch-decision packet. It contains the launch bindings and
current controls. The mutable checklist belongs in the
[execution runbook][runbook]; neither document should accumulate run histories
or evidence appendices.

[completion-plan]: https://github.com/leanprover/lean-eval/blob/main/docs/overhaul-completion-plan.md#75-production-launch-readiness-packet
[runbook]: https://github.com/leanprover/lean-eval/blob/main/docs/overhaul-execution-runbook.md

## 1. Launch and current runtime bindings

Protected launch and current operational bindings:

| Repository | Binding |
| --- | --- |
| `leanprover/lean-eval` | Launch-copy milestone `313078a0962c4a929d790772a7bf2f997f22857b` |
| `leanprover/lean-eval-submissions` | Current deployed Worker and replay-controller source `e28473c5c83764a044cb9d2666e1b815517e6d0e`; State staging/promotion milestone `36e405e558be69d50e3093d3e188d24d6fc7cfa1`. |
| `leanprover/lean-eval-leaderboard` | Current protected temporary-pause copy `2b886b70d63b7c39b4efa1af744746b0e434d34c`; the launch copy is retained separately for later restore |
| `leanprover/lean-eval-state` | Retained-baseline promotion checkpoint `76b3b3e54f4be69161a00cd81576a58df8eae815`, tree `e196521b812a0942eea9d11a8bcb2d7569728d50`; resolve the live append-only head before every later bound operation |
| `leanprover/lean-eval-state-staging` | Retained final launch-acceptance checkpoint `c604bb446a51fc833c96887053ec64672c912d8c`; resolve the live append-only head when needed |
| `leanprover/lean-eval-releases` | `dbd9d7ca947e28b041fbb1b72667f0283265189f` |
| `leanprover/lean-eval-generator` | `010b01634cccda2db538cf9b09e6f26ddc453743` |
| `leanprover/lean-eval-audit` | Retained-baseline migration checkpoint `d73132415738b0d82c99fd43f630804fe996e342`; tree `48c24fc428eea77d7d9320133fd978f8c7b6abfc`; resolve live append-only `main` before later replay operations |

The protected submissions `staging-results` branch is
`1deb87414faf64edfa31639a8430fcf98fb2ccb5`.

Current production and staging runtime bindings:

| Boundary | Exact runtime and effective gates |
| --- | --- |
| Production submission unit | Protected implementation `e28473c5c83764a044cb9d2666e1b815517e6d0e`; intake and all six lifecycle/publication gates are disabled pending dual-App source-admission deployment and staging proof. Re-read exact Cloudflare versions before promotion. |
| Production replay | Protected implementation `e28473c5c83764a044cb9d2666e1b815517e6d0e`; general replay, historical-public replay, and staging acceptance are disabled. Production replay credentials are installed, and both historical controller variables are absent. Re-read exact Cloudflare versions before mutation. |
| Current staging runtime | Protected implementation `e28473c5c83764a044cb9d2666e1b815517e6d0e`; intake and lifecycle APIs are disabled, promotion canary and staging acceptance are enabled, and general/historical replay is disabled. Re-read exact Cloudflare versions before promotion. |
| Retained-baseline State batch | Promoted commit `76b3b3e54f4be69161a00cd81576a58df8eae815`, tree `e196521b812a0942eea9d11a8bcb2d7569728d50`; binding SHA-256 `e2b95a76d5d854f27d95358a2aafd380a40acc8445c3ab13ae7621614ce8d31f`; 2,439 events materialize 174 public and 639 private replay tasks; the fixed review branch is absent. |
| Current historical queues | Both controller variables are absent, neither lane has an active task, and no successor is enabled. Re-read protected State for current queue counts. |
| Release controller | Protected commit `dbd9d7ca947e28b041fbb1b72667f0283265189f`; `release-production` environment variable `PUBLICATION_ENABLED` is `true`. The production canary is scheduled for `2026-11-02T03:50:01.002Z`; its source is not yet due. |
| Public entry and leaderboard | Protected leaderboard `2b886b70d63b7c39b4efa1af744746b0e434d34c` is the temporary-pause copy: problem statements remain visible and issue intake is primary while server intake is disabled. Re-read the live Pages deployment before relying on it. |

The immutable dispatch ref for the protected submissions candidate is
`lean-eval-dispatch/e28473c5c83764a044cb9d2666e1b815517e6d0e`.
The automatic release controller remains enabled. Production intake and all
six lifecycle/publication gates are disabled pending the dual-App admission
repair and staging proof. General Worker replay, historical successors, model
consolidation, publication opt-out, and the production promotion canary remain
disabled.

## 2. Launch gate status

The launch boundaries below record current status:

- schema-version-3 archive-before-evaluation with one per-submission envelope,
  strict submission/digest binding, production Encrypt-only Wrap authority,
  and no evaluation-lane wrap or unwrap authority;
- publication-disabled staging reconstruction with one-submission scope,
  consume-before-unwrap, reuse refusal, source allowlisting, no plaintext
  artifact, and no State mutation;
- production release OIDC, audit-read, State-write, and unwrap-invoker scope,
  with no production decrypt or publication performed during preflight;
- protected production State contract and event-schema readiness;
- retained staging browser OAuth and source-bound headless archive, evaluation,
  and Result qualification;
- launch-approved optional lifecycle routes, authorization denials, and
  redaction through repository tests and prior staging route evidence; a second
  staging route matrix is not a launch gate;
- one-way publication opt-in remains launch-enabled behind its feature flag;
  the production canary supplies its launch proof;
- production all-false recovery and coherent rollback; and
- lifecycle-aware leaderboard output with stable problem pages and visible
  statements.

The table records the completed launch binding and audit bootstrap
verification.

| Phase 4 finalization category | Required exact final binding | Current status |
| --- | --- | --- |
| Final staging smoke | Browser and source-bound headless paths; terminal archive, evaluation, Result, State, scheduled release, and all-false cleanup | **Pending repair proof:** rerun the staging promotion canary after deploying dual-App repository and exact-commit admission. |
| Automatic release | Protected releases `dbd9d7ca947e28b041fbb1b72667f0283265189f`; scoped credentials and write-free no-op controls; `release-production` environment variable `PUBLICATION_ENABLED=true` | **Complete:** the enabled controller has one canary release scheduled for `2026-11-02T03:50:01.002Z`; no source is due now. |
| Production lifecycle APIs | Exact protected submissions commit and dispatch tag; effective-health readback showing only the approved lifecycle and publication-opt-in families enabled while intake, consolidation, opt-out, and replay remain false; one non-mutating authorization denial | **Pending restore:** all six lifecycle/publication gates are currently disabled until the repaired intake boundary is qualified. |
| Production intake lease and durable transition | Exact merged intake commit and dispatch tag; provisional-disabled and finite-lease version/readback; lease start and expiry; one-use smoke result; protected State head recheck; final durable intake, broker, and replay effective health | **Pending:** production intake remains disabled until the dual-App admission boundary is deployed, proven in staging, and explicitly launched. |
| Production canary | Submission ID and packet-bound source/model identity; archive and evaluation terminal state; Result and State identity; initial withheld presentation; irreversible opt-in and scheduled presentation | **Complete:** submission `01a0603c-6189-7751-9c43-c904b50b477a` produced Result `r2_176e0f46710a69d54b3cbcc722a948b364de2acdf2a1ee6fe667f0a331254a59`; its one-way opt-in is scheduled for `2026-11-02T03:50:01.002Z`. Production State was observed at `fb70dd6ba14cae94b30d570818e4801884e81e04` after these terminal events and may advance append-only. The launch canary fixture branch was removed after that run. Current access by both Apps is a separate staging prerequisite proved only by the exact promotion canary. |
| Production pause | Exact pre-pause release, intake, broker, replay, and State heads; all-false recovery action and disabled readback; publication-disabled no-op preflight and unchanged State | **Complete:** the exact production all-false pause was exercised and verified. |
| Ordered restore | Separate release-controller, lifecycle-with-intake-disabled, and finite-lease-to-durable intake restore actions; exact commit/effective-health, publication posture, and protected-State readback after each action | **Pending repair promotion:** release remains enabled; lifecycle and intake restore follow dual-App staging proof. |
| Server-primary entry | Protected and deployed leaderboard commit/build/readback for the server-primary page with issue fallback; protected LeanEval launch-copy commit; verified live entry URL and security/license/release text | **Paused:** protected leaderboard `2b886b70d63b7c39b4efa1af744746b0e434d34c` is the temporary issue-primary copy. The reviewed server-primary launch copy and LeanEval `313078a0962c4a929d790772a7bf2f997f22857b` remain prepared for restore after repaired intake is qualified. |
| Overlap announcement | Explicit UTC announcement time, UTC overlap start, target closure no earlier than four weeks later, issue-intake fallback URL, and subsequent closure-notice date at least two weeks before any closure | **Complete:** overlap began `2026-09-02T06:57:10Z`; issue intake remains available through no earlier than `2026-09-30T06:57:10Z`. |
| Audit bootstrap | Protected audit target and bootstrap contract verification | **Complete:** audit `main` `7a53c75c6d7c263c684ebcd54590c657c9298642`, tree `4e44c06`. |

## 3. Launch presentation

The public entry is temporarily issue-primary while server intake is disabled.
The server-primary copy remains prepared for restore after the dual-App repair
is qualified. Issue intake remains available through no earlier than
`2026-09-30T06:57:10Z`.

## 4. Credentials, ownership, and recovery

[`INFRASTRUCTURE.md`](../INFRASTRUCTURE.md) is the identifier and procedure
ledger. Kim Morrison is the temporary runtime-secret and OAuth custodian unless
organization ownership supplies recovery.

| Credential class | Scope and owner | Rotation, revocation, and recovery |
| --- | --- | --- |
| Cloudflare deploy token | One dedicated `lean-eval` account; Workers Scripts and Containers edit only; separate staging and production copies; no DNS or zone permission. Kim Morrison is custodian. | Replace one environment secret, verify its intended state, then revoke the old token. |
| Worker readiness, callback, and session secrets | `READINESS_TOKEN` and `LIFECYCLE_CALLBACK_TOKEN` are shared only by one Worker and its matching protected environment; `AUTH_TOKEN_SECRET` signs only that environment's sessions. | Rotate each Worker/environment pair independently. Overwrite both copies to revoke shared tokens. Replacing the auth secret intentionally invalidates that environment's sessions. Keep intake disabled until replacement verification succeeds. |
| Production State writer | Contents write only on `leanprover/lean-eval-state`; expires `2026-11-19`; Kim Morrison is custodian. | Install and verify an equally scoped replacement by `2026-11-05`, then revoke the old token. Loss or rate exhaustion fails readiness and intake closed. |
| GitHub Apps | Organization-owned Source Reader App `4666604` has metadata/contents read only on opted-in source repositories. Temporary legacy workflow reader `lean-eval-bot` (`3346375`) has contents read on opted-in source repositories. Workflow Dispatcher App `4666633` has metadata/contents/actions only on `leanprover/lean-eval-submissions`. | The protected deployment copies existing `LEAN_EVAL_BOT_*` repository secrets directly into the private brokers without exposing values. Intake requires both source readers to prove repository identity and the exact commit. Rotate and verify one key before deleting its predecessor. |
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
   last recorded coherent rollback unit is commit
   `b6f8c8834213a26a19ba1e8c7440db30ad0c05f2`, intake
   `98e1d29e-aa81-4fa5-b095-ac2261d7f9a0`, replay
   `8dabd811-9e81-4a37-95c2-5290b07fbabb`, and broker
   `30d025fd-aa30-40d5-9cbb-a1762fc99725`.
5. Never rewrite State, Results, releases, audit objects, AWS data, credentials,
   or Git history as rollback.

## 7. Deferred or deliberately disabled

- The retained-baseline private-envelope migration and State promotion are
  complete. The non-replenishing migrated-envelope private canary reached
  `replay.accepted`, and its cleanup, artifact, resource, scrub, and Audit checks
  passed. Both controller variables are absent and the bounded two-lane drain is
  inactive.
- Model consolidation remains disabled. Publication opt-out remains absent.
- FC integration, disproof support, experimental kernels, persistent
  qualification machinery, model-consolidation launch, automatic copycat
  detection, a second key provider, and verified-calculation runner
  infrastructure are outside this overhaul.
- The neutral open-problems tab may remain empty.
- Issue intake remains available for at least the announced four-week overlap
  and closes only after the separate incident, adoption, notice, and final-data
  gates pass.

## 8. Launch completion

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
launch fixture branch was removed. Current App access to the fixed staging
fixture is not inferred from that earlier run; the exact promotion canary must
prove both Apps can read its repository and commit. The earlier launch exercise
completed, but server intake and server-primary presentation are now
temporarily paused pending the dual-App repair. The overlap began
`2026-09-02T06:57:10Z`; issue intake is currently primary and remains available
through no earlier than `2026-09-30T06:57:10Z`.
