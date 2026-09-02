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
| `leanprover/lean-eval-submissions` | `f03f5cde4f1ac83b13ce78f294fc2273980dbf0a` |
| `leanprover/lean-eval-leaderboard` | `d7f0de9d9b5abbb62a4080df31002825a1afa814` |
| `leanprover/lean-eval-state` | `7ffb7ffb78d79847137785c65df25770f41b62ef` |
| `leanprover/lean-eval-state-staging` | `c604bb446a51fc833c96887053ec64672c912d8c` |
| `leanprover/lean-eval-releases` | `dbd9d7ca947e28b041fbb1b72667f0283265189f` |
| `leanprover/lean-eval-generator` | `010b01634cccda2db538cf9b09e6f26ddc453743` |
| `leanprover/lean-eval-audit` | `666950ce7702d1d2a1392b12f9104781ac9446e3` |

The protected submissions `staging-results` branch is
`1deb87414faf64edfa31639a8430fcf98fb2ccb5`.

Current deployed state:

| Boundary | Exact runtime and effective gates |
| --- | --- |
| Production submission unit | Commit `451856ebdd4ca4d875e43be7cd113678dea9e1b7`; intake disabled; every public lifecycle family disabled; both maintainer lists empty; model consolidation, publication opt-out, and promotion canary disabled. Active Cloudflare versions: intake `7afc61bf-6427-431a-b4f6-c1c3ec2641ac`, broker `dfd77e4f-16ae-4a63-81ab-bbb79797385b`, replay `570664e6-a6f5-428d-87cb-803dd5b1768f`. |
| Production replay | Commit `451856ebdd4ca4d875e43be7cd113678dea9e1b7`; general replay, historical-public replay, and staging acceptance disabled. |
| Staging submission unit | Commit `f03f5cde4f1ac83b13ce78f294fc2273980dbf0a`; intake version `c55e2220-393a-4739-b0ad-71d8eb455dc2`, broker version `b93729b0-dfac-4fba-bf9b-12d318e2111f`; intake and every public lifecycle gate are false; both maintainer lists are empty; model consolidation and publication opt-out are false. The staging-only promotion canary is true. |
| Staging replay | Commit `f03f5cde4f1ac83b13ce78f294fc2273980dbf0a`; version `c91f96f2-a0f8-4900-a951-b8f26eaceef9`, container application version `22`; general and historical-public replay disabled; staging acceptance enabled. |
| Release controller | Protected commit `dbd9d7ca947e28b041fbb1b72667f0283265189f`; production State projection is pinned to `7ffb7ffb78d79847137785c65df25770f41b62ef`; publication variable `PUBLICATION_ENABLED` is `true`. The exact enabled controller produced an empty or not-due plan and did not change State or audit. |
| Public entry and leaderboard | Protected and deployed leaderboard `d7f0de9d9b5abbb62a4080df31002825a1afa814` retains stable lifecycle-aware problem pages and visible statements. `https://lean-lang.org/eval/submit/` remains issue-primary and the production Worker remains disabled. The deployed projection maps a modern Result with no release task to `withheld`; its first production readback belongs to the production canary. |

The immutable dispatch ref for the protected submissions candidate is
`lean-eval-dispatch/f03f5cde4f1ac83b13ce78f294fc2273980dbf0a`.
The automatic release controller is enabled. Production server intake,
lifecycle APIs, replay, model consolidation, and publication opt-out remain
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
| Final staging smoke | Browser `01a05f9a-a349-7b40-91e3-31082d2e99f3` is terminal through archive, accepted Result `r2_9b791dd9ba2d4383e0065bc791bb19f8f81d9e4d8a21f7470d806057e8e9a90c`, and State. Headless `01a05f9e-0291-77a1-b80f-de07610fd3ad` is terminal through evaluation and Result `r2_93e1c1d18533806f33531603858059536c2ce7d3cb838f5f67b70699d27d6198` at `staging-results` `1deb87414faf64edfa31639a8430fcf98fb2ccb5`; audit repository head is `666950ce7702d1d2a1392b12f9104781ac9446e3`; result event `01a05fca-f7fa-77a6-82d6-5dd0c22fc959` and scheduled-release event `01a05fca-f7fb-7dd4-92ed-8f3487cb0ee4` for `2026-11-02T00:57:18.002Z` are at State `c604bb446a51fc833c96887053ec64672c912d8c`. | **Complete:** protected-main State validation [`33582505523`](https://github.com/leanprover/lean-eval-state-staging/actions/runs/33582505523) passed; all-false intake `c55e2220-393a-4739-b0ad-71d8eb455dc2`, broker `b93729b0-dfac-4fba-bf9b-12d318e2111f`, and replay `c91f96f2-a0f8-4900-a951-b8f26eaceef9` are bound; effective health is all false except the staging promotion canary; watchdog [`33579467307`](https://github.com/leanprover/lean-eval-submissions/actions/runs/33579467307) is cancelled; bounded staging-fixture removal is verified. |
| Automatic release | Protected releases `dbd9d7ca947e28b041fbb1b72667f0283265189f`; publication-disabled credential, audit-read, OIDC, and write-free no-op preflights; `PUBLICATION_ENABLED=true`; enabled controller run [`33583140345`](https://github.com/leanprover/lean-eval-releases/actions/runs/33583140345) | **Complete:** protected-main validation [`33582979345`](https://github.com/leanprover/lean-eval-releases/actions/runs/33582979345) and preflights [`33583050588`](https://github.com/leanprover/lean-eval-releases/actions/runs/33583050588), [`33583051949`](https://github.com/leanprover/lean-eval-releases/actions/runs/33583051949), [`33583053537`](https://github.com/leanprover/lean-eval-releases/actions/runs/33583053537), and [`33583055166`](https://github.com/leanprover/lean-eval-releases/actions/runs/33583055166) passed. The disabled no-op plan was `empty`; the enabled run also found no due work and skipped unwrap. Production State remains `7ffb7ffb78d79847137785c65df25770f41b62ef` and audit remains `666950ce7702d1d2a1392b12f9104781ac9446e3`. |
| Production lifecycle APIs | Exact protected submissions commit and dispatch tag; production intake, broker, and replay version IDs; effective-health readback showing only the approved lifecycle and publication-opt-in families enabled while intake, consolidation, opt-out, and replay remain false; one non-mutating authorization denial | **Pending separate lifecycle deployment after packet `GO`.** |
| Production intake lease and durable transition | Exact merged intake commit and dispatch tag; provisional-disabled and finite-lease version/readback; lease start and expiry; one-use smoke result; protected State head recheck; final durable intake, broker, and replay versions and effective health | **Pending a clean intake-only descendant after the lifecycle deployment.** |
| Production canary | Submission ID and packet-bound source/model identity; archive commit/path and digests; evaluation run and terminal Result identity; terminal State and Results heads; exact leaderboard deployed head/build/URL showing initial `Release withheld`; irreversible opt-in event and subsequent scheduled presentation; release-readiness binding | **Withheld projection contract deployed at leaderboard `d7f0de9d9b5abbb62a4080df31002825a1afa814`; actual withheld and scheduled cards pending durable production intake and the at-most-once canary.** |
| Production pause | Exact pre-pause release, intake, broker, replay, and State heads; all-false recovery action and disabled version/readback; publication-disabled no-op preflight and unchanged State; disposition of any active release run | **Pending successful canary.** |
| Ordered restore | Separate release-controller, lifecycle-with-intake-disabled, and finite-lease-to-durable intake restore actions; exact commit/version, effective-health, publication posture, and protected-State readback after each action | **Pending successful pause proof.** |
| Server-primary entry | Protected and deployed leaderboard commit/build/readback for the server-primary page with issue fallback; protected LeanEval launch-copy commit; verified live entry URL and security/license/release text | **Pending restored production service and refreshed launch-copy candidate `#603`.** |
| Overlap announcement | Explicit UTC announcement time, UTC overlap start, target closure no earlier than four weeks later, issue-intake fallback URL, and subsequent closure-notice date at least two weeks before any closure | **Pending verified server-primary deployment; no dates are claimed yet.** |

## 3. Held launch presentation change

This packet branch adds only the reviewed publication-default copy
`f4b21d74fbf4891f72b7d2bb9cc33383042795a1` to the protected f03 runtime;
production intake remains disabled. Intake enablement is a later
single-purpose descendant and must not be folded into this pull request.

| Draft | Exact candidate | Intended action |
| --- | --- | --- |
| [`lean-eval#603`](https://github.com/leanprover/lean-eval/pull/603) | Existing remote head `4ac6205ea122278db1101d4f5cd91cb4d28a954d` is stale; a reviewed replacement is prepared for rebasing onto protected `cd6fc7c27fa5227b29b610558290c73994ffe84e` | Make repository copy server-primary while retaining issue intake during the overlap. Update the draft only after server intake and the matching leaderboard entry are public. |

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
   code with current secrets and finishes with production intake disabled.
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

Only these actions are pending, in order. Fill the matching finalization row
above and keep the mutable runbook current after each fact exists; do not
prefill terminal values.

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
| Expected Result ID | `r2_176e0f46710a69d54b3cbcc722a948b364de2acdf2a1ee6fe667f0a331254a59` |
| Source Reader repository-access preflight | [successful run `33557767912`](https://github.com/leanprover/lean-eval-submissions/actions/runs/33557767912) at `f03f5cde4f1ac83b13ce78f294fc2273980dbf0a` |
| Evaluation App exact-ref preflight | [successful run `33557770740`](https://github.com/leanprover/lean-eval-submissions/actions/runs/33557770740) at `f03f5cde4f1ac83b13ce78f294fc2273980dbf0a` |

Submit that exact tuple at most once. After its archive, evaluation, State,
Result, leaderboard presentation, irreversible private-to-scheduled event, and
every dependent run are terminal, delete the fixture branch. Only then remove
`leanprover/lean-eval-state-staging` from both read-only source App
installations. The source-App cleanup does not wait for the scheduled source's
two-month publication date.

1. **Deploy production lifecycle APIs.** Reconfirm server production is
   all-false and
   deploy the exact protected descendant of copy candidate
   `f4b21d74fbf4891f72b7d2bb9cc33383042795a1`, leaving intake disabled while
   enabling only the launch-approved owner, maintainer, alias/rename, and
   one-way publication-opt-in families. Keep consolidation and opt-out false.
2. **Enable server intake.** Prepare and merge the clean intake-only descendant,
   and require the deployment to pass the
   provisional-disabled checks, finite lease, one-use smoke, protected-State
   recheck, and final durable transition.
3. **Verify withheld presentation with the canary.** The exact reviewed
   leaderboard `#90` projection is deployed from protected main and the
   prelaunch submit page remains issue-primary. Production State contains no
   modern Result yet, so verify its first actual `Release withheld` card as the
   initial presentation step of the at-most-once canary below.
4. **Run one production canary.** Submit the packet-bound private canary,
   verify archive-before-evaluation, terminal State and Result, leaderboard
   presentation, and release readiness, then exercise the visible irreversible
   private-to-scheduled opt-in. Force a leaderboard build and verify both the
   initial withheld state and the scheduled transition. On any failure, run
   all-false recovery and pause publication.
5. **Prove production pause and restore.** Execute section 6 against the exact
   deployed production versions. Restore release, lifecycle, and intake as
   separate actions in that order, with effective-health and protected-State
   readback after each action.
6. **Make the server primary and begin overlap.** After restored intake is
   publicly usable, merge and deploy a single-purpose leaderboard change that
   makes `https://lean-lang.org/eval/submit/` server-primary while preserving
   issue intake as the overlap fallback. Rebase, revalidate, and merge
   launch-copy draft `#603` against current protected LeanEval. Verify the live
   entry page before publishing the server URL, explicit UTC overlap start and
   target closure dates, issue fallback, and security/license/release contract.
   Zulip or comments on other repositories remain separately authorized
   external communication.

The packet is `GO`. Actions 1–6 proceed in order, one capability at a time,
with pause or rollback after any failed readback.
