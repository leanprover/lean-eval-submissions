# LeanEval lifecycle-overhaul implementation tracker

Status: **active**

Scope authority: [LeanEval completion plan][completion-plan]

Execution authority: [LeanEval execution runbook][execution-runbook]

[completion-plan]: https://github.com/leanprover/lean-eval/blob/main/docs/overhaul-completion-plan.md
[execution-runbook]: https://github.com/leanprover/lean-eval/blob/main/docs/overhaul-execution-runbook.md

This file records only current repository state and the remaining
`lean-eval-submissions` work. It does not define product scope, preserve a run
history, or replace the cross-repository execution runbook.

## Current safe baseline

- Protected `main` requires the `verify` aggregate check.
- Production Workers report one coherent durable live deployment at
  `b6f8c8834213a26a19ba1e8c7440db30ad0c05f2`.
- Production intake, the six approved lifecycle gates, and one-way publication
  opt-in are enabled. Both production maintainer lists contain exactly
  `kim-em` / GitHub user `477956`. General Worker replay, the bounded historical
  drain, model consolidation, publication opt-out, and the promotion canary
  remain disabled.
- The production canary is terminal and scheduled for
  `2026-11-02T03:50:01.002Z`. The retained historical baseline is promoted in
  production State at `76b3b3e54f4be69161a00cd81576a58df8eae815`, tree
  `e196521b812a0942eea9d11a8bcb2d7569728d50`; the append-only head may
  advance during ordinary intake. The canary fixture branch and temporary App
  repository access are removed.
- The exact production all-false pause and ordered release/lifecycle/intake
  restore are complete. Server-primary presentation is live. The overlap began
  `2026-09-02T06:57:10Z`, and issue intake remains available through no earlier
  than `2026-09-30T06:57:10Z`.
- Tracked staging intake and every staging lifecycle API remain all-false with
  empty maintainer allowlists.
- General staging replay, historical-public replay, and production replay are
  disabled.
- Model consolidation remains disabled in both tracked environments and is not
  a launch feature.
- Automatic release publication is enabled in
  `leanprover/lean-eval-releases`; the canary source is not yet due.
- Production archive Wrap is connected and qualified. The production replay
  role and State, audit, and Cloudflare credentials are installed. The public
  and private historical controller variables are both absent. The
  non-replenishing migrated-envelope private canary completed successfully;
  protected State `d223853a90b37a51d4bbfac30c8213cf78be5778`
  materializes 174 public and 637 private queued tasks. General Worker replay
  remains disabled, and the bounded drain is not enabled.
- The staging release role trusts the exact repository/environment GitHub OIDC
  subject, and its credentialed, publication-disabled reconstruction boundary
  is qualified.
- Exact resource identifiers, credential custody, feature states, and rollback
  instructions are in [`../INFRASTRUCTURE.md`](../INFRASTRUCTURE.md).

The source of truth for a live health observation is the public structured
health response checked by
[`lifecycle-readiness-monitor.yml`](../.github/workflows/lifecycle-readiness-monitor.yml),
not this summary.

## Retained implementation

- [x] Results schema version 2 migration and immutable Results base records.
- [x] Append-only production and staging State contracts and materialized
      views.
- [x] Browser OAuth and source-bound headless-agent authentication.
- [x] Archive-before-evaluation with schema-version-3 per-submission key
      envelopes and strict digest/submission binding.
- [x] Accepted, rejected, archive-failed, and evaluation-failed lifecycle
      transitions.
- [x] Metadata backfill, repair/retraction request, maintainer-decision, and
      model-alias/rename implementation behind independent gates. This
      candidate enables only the reviewed production surface; staging and the
      separate compatible rollback baseline remain all-false.
- [x] Official Lean build and nanoda replay formats with versioned checker and
      measurement fields.
- [x] Protected finite-lease intake enablement and disable-only recovery.
- [x] Deterministic release reconstruction with publication disabled.

## Remaining repository work

### Disabled-state reconciliation

- [x] Confirm the latest protected deployment and readiness monitor are both
      green at one exact commit.
- [x] Confirm production and staging State heads validate and match the tracked
      contract pins.
- [x] Confirm production State contains no unexpected accepted server result or
      due release work.
- [x] Keep the infrastructure inventory current without adding run narratives.

### Launch preparation

- [x] Prepare one success and one authorization/validation denial fixture for
      metadata backfill.
- [x] Prepare one success and one authorization/validation denial fixture for
      repair/retraction requests.
- [x] Prepare one success and one authorization/validation denial fixture for
      maintainer decisions.
- [x] Prepare one success and one authorization/validation denial fixture for
      model alias/rename.
- [x] Preserve the bounded f09 browser/headless, owner/maintainer denial,
      private-to-scheduled opt-in, publication-disabled reconstruction,
      leaderboard, and all-false recovery results as unchanged-feature
      evidence.
- [x] Prove that each launch gate returns to disabled and public health reports
      the effective state.
- [x] Retain one coherent disable-only rollback unit at
      `b6f8c8834213a26a19ba1e8c7440db30ad0c05f2` and keep its exact component
      versions in the infrastructure inventory and rollout runbook.
- [x] Bind the protected lifecycle and intake deployment to immutable dispatch,
      complete the production canary, and verify the all-false pause and
      ordered restore.
- [x] Make server intake primary in the leaderboard entry and begin the
      four-week overlap at `2026-09-02T06:57:10Z`.

Rerun a functional case only if its implementation path changes or the fresh
checks expose drift. This is a bounded launch check, not a qualification
campaign.

### Credential-boundary work

- [x] Review the exact staging release OIDC trust mutation, target role and
      subject, absent publication/production authority, test archive, and
      rollback.
- [x] Apply only the reviewed staging trust mutation.
- [x] Complete one staging unwrap/reconstruction and verify
      consume-before-unwrap, reuse refusal, authority removal, source
      allowlisting, no plaintext artifact, and cleanup.
- [x] Connect the production archive Wrap-only role and prove it cannot unwrap
      before intake is enabled.
- [x] Repair and reverify the production release role trust and scope without
      decrypting or publishing a production archive.

### Historical completion

- [x] Freeze the 128-problem v1 historical scope.
- [x] Reconcile each in-scope Result as public-source, private-archive, or
      reviewed unavailable.
- [x] Migrate the recoverable private archives to bound schema-version-3
      envelopes without changing archive ciphertext or stable IDs.
- [x] Run the official Lean kernel path and nanoda with bounded retries.
- [x] Record a terminal replay or reviewed-unavailable disposition for every
      in-scope Result.
- [x] Disable both controller gates and confirm no replay remains running.
- [ ] Remove the temporary replay and qualification machinery after the final
      private resource inventory proves empty.

Canonical inputs retained for this work:

- [`historical-replay-inventory.md`](historical-replay-inventory.md)
- [`historical-public-replay-plan.md`](historical-public-replay-plan.md)
- [`historical-public-replay-profiles.md`](historical-public-replay-profiles.md)
- [`historical-private-archive-crosswalk.md`](historical-private-archive-crosswalk.md)
- [`historical-public-unavailability.md`](historical-public-unavailability.md)
- machine-readable inputs under `evidence/historical-replay/` and
  `evidence/public-replay/`

The 145 materialized public tasks outside the v1 problem set are deferred and
do not block this overhaul. Retain their canonical data so a later project can
replay them without another archive migration.

## Standing authorization boundary

Repository implementation, tests, PRs, merges, ordinary CI, and automatic
disabled-state deployments are autonomous inside the allowlisted LeanEval
repository family. Standing maintainer authorization also covers every
remaining in-scope infrastructure, credential, production, canonical-data,
issue-retirement, announcement, and external non-PR operation. Readiness
packets, preconditions, review, rollback, and post-change verification remain
mandatory; authenticated maintainer steps are operator handoffs, not new
permission gates.

Stop for exact maintainer approval only before:

- opening, updating, or merging a pull request outside the allowlist;
- posting a Zulip message or comment;
- posting a comment or review on another person's pull request; or
- expanding the completion-plan scope.

The launch readiness packet and capability-enable sequence are maintained in
the [cross-repository execution runbook][execution-runbook].

## Definition of done for this repository

- [x] New submissions archive before evaluation with a per-submission envelope.
- [x] Accepted and rejected lifecycle transitions are coherent and recoverable.
- [x] Launch-approved owner and maintainer APIs are operating with disable
      paths.
- [x] Automatic releases operate under the two-calendar-month policy with the
      initial private/scheduled choice and one-way later opt-in.
- [ ] Every final-cutoff accepted Result has an official-Lean-plus-nanoda
      terminal replay or reviewed unavailable disposition.
- [x] Current rollback and emergency-pause procedures are verified.
- [x] No tracked instruction asks for work outside the completion-plan scope.
