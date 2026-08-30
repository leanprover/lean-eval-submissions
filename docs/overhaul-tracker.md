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
- Staging and production Workers report one coherent live deployment at
  `30bc92b3d46bd2a3ba1788433264fdd70ae3c74e`.
- The live deployment keeps production intake and every public production
  lifecycle API configured and effectively disabled, with empty maintainer
  allowlists.
- This launch candidate keeps production intake disabled while tracking the
  reviewed result-owner, amendment-owner, amendment-maintainer,
  model-identity-owner, model-identity-maintainer, and release-opt-out gates as
  enabled for exactly `kim-em` / GitHub user `477956` where maintainer
  authority is required. It has not been deployed and readiness remains
  `NO-GO`.
- Tracked staging intake and every staging lifecycle API remain all-false with
  empty maintainer allowlists.
- General staging replay, historical-public replay, and production replay are
  disabled. The bounded staging acceptance endpoint remains enabled.
- Model consolidation remains disabled in both tracked environments and is not
  a launch feature.
- Automatic release publication is disabled in
  `leanprover/lean-eval-releases`.
- Production archive Wrap is connected and qualified; the production replay
  role variable is not connected.
- The staging release role trusts the exact current ID-bearing GitHub OIDC
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
      model-alias/rename implementation behind independent gates. This launch
      candidate enables the reviewed production lifecycle surface only;
      staging and the live production lifecycle surface remain all-false.
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
- [x] Prepare one pre-release opt-out case and verify its scheduling effect.
- [x] Prove that each launch gate returns to disabled and public health reports
      the effective state.
- [ ] Prepare one browser and one source-bound headless staging submission at
      the exact proposed launch commits. Its source commit and one-time tag are
      confined to a temporary non-default branch in the allowlisted private
      staging State repository; only the target-bound secret-Gist proof remains
      outside the repository family and is covered by standing authorization.
- [ ] Complete one exact-version staging lifecycle from archive through State,
      Result, scheduled release, publication-disabled reconstruction, and
      rollback.

These are bounded smoke cases, not a combinatorial staging matrix.

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
- [ ] Repair and reverify the production release role trust and scope using
      [`aws-release-production-trust-repair.md`](aws-release-production-trust-repair.md),
      without decrypting or publishing a production archive.

### Historical completion

- [ ] Freeze the final issue-intake cutoff and generate the append-only
      inventory delta.
- [ ] Reconcile every accepted Result as public-source replayable,
      private-archive replayable, or reviewed unavailable.
- [x] Freeze all thirty-five exact historical-public execution profiles.
      State qualification and enqueue remain gated on the exact immutable
      execution packet.
- [x] Provide one offline, create-only batch finalizer for the existing
      authorize, qualify, and enqueue State events across the exact retained
      public task subset. It derives terminal exclusions from the validated
      pinned State ledger; appending its output remains gated on that packet.
- [ ] Migrate recoverable legacy private envelopes only after the migration
      execution packet is complete and the custodian operator handoff is ready.
- [ ] Run bounded official-Lean-plus-nanoda replay and record a terminal replay
      or unavailable disposition for every final-cutoff Result.

Canonical inputs retained for this work:

- [`historical-migration-replay-execution-packet.md`](historical-migration-replay-execution-packet.md)
- [`historical-replay-inventory.md`](historical-replay-inventory.md)
- [`historical-public-replay-plan.md`](historical-public-replay-plan.md)
- [`historical-public-replay-profiles.md`](historical-public-replay-profiles.md)
- [`historical-private-archive-crosswalk.md`](historical-private-archive-crosswalk.md)
- [`historical-public-unavailability.md`](historical-public-unavailability.md)
- machine-readable inputs under `evidence/historical-replay/` and
  `evidence/public-replay/`

Build or qualify only exact images used by replayable Results. Execute the
official Lean kernel path and nanoda only.

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

- [ ] New submissions archive before evaluation with a per-submission envelope.
- [ ] Accepted and rejected lifecycle transitions are coherent and recoverable.
- [ ] Launch-approved owner and maintainer APIs are operating with disable
      paths.
- [ ] Automatic releases operate under the two-calendar-month policy with
      opt-out support.
- [ ] Every final-cutoff accepted Result has an official-Lean-plus-nanoda
      terminal replay or reviewed unavailable disposition.
- [ ] Current rollback and emergency-pause procedures are verified.
- [ ] No tracked instruction asks for work outside the completion-plan scope.
