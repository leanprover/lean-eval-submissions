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
- Staging and production Workers report one coherent deployed commit.
- Production intake is configured and effectively disabled.
- General staging replay, historical-public replay, and production replay are
  disabled. The bounded staging acceptance endpoint remains enabled.
- Public result-owner, maintainer, model-alias, and model-rename APIs are
  disabled. Maintainer allowlists are empty.
- Model consolidation is not a launch feature.
- Automatic release publication is disabled in
  `leanprover/lean-eval-releases`.
- Production archive Wrap and replay role variables are not connected.
- The staging release role still needs the approval-gated OIDC trust repair.
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
      model-alias/rename implementation behind disabled gates.
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
      the exact proposed launch commits.
- [ ] Complete one exact-version staging lifecycle from archive through State,
      Result, scheduled release, publication-disabled reconstruction, and
      rollback.

These are bounded smoke cases, not a combinatorial staging matrix.

### Approval-gated credential work

- [ ] Present the exact staging release OIDC trust mutation, target role and
      subject, absent publication/production authority, test archive, and
      rollback for approval.
- [ ] After approval, run one staging unwrap/reconstruction and verify
      consume-before-unwrap, reuse refusal, authority removal, source
      allowlisting, no plaintext artifact, and cleanup.
- [ ] Present the production archive Wrap-only connection separately for
      approval; prove it cannot unwrap before intake is enabled.
- [ ] Repair and reverify the production release role trust and scope using
      [`aws-release-production-trust-repair.md`](aws-release-production-trust-repair.md),
      without decrypting or publishing a production archive.

### Historical completion

- [ ] Freeze the final issue-intake cutoff and generate the append-only
      inventory delta.
- [ ] Reconcile every accepted Result as public-source replayable,
      private-archive replayable, or reviewed unavailable.
- [ ] Migrate recoverable legacy private envelopes only after the separate
      infrastructure and credential approval.
- [ ] Run bounded official-Lean-plus-nanoda replay and record a terminal replay
      or unavailable disposition for every final-cutoff Result.

Canonical inputs retained for this work:

- [`historical-replay-inventory.md`](historical-replay-inventory.md)
- [`historical-public-replay-plan.md`](historical-public-replay-plan.md)
- [`historical-public-replay-profiles.md`](historical-public-replay-profiles.md)
- [`historical-private-archive-crosswalk.md`](historical-private-archive-crosswalk.md)
- [`historical-public-unavailability.md`](historical-public-unavailability.md)
- machine-readable inputs under `evidence/historical-replay/` and
  `evidence/public-replay/`

Build or qualify only exact images used by replayable Results. Execute the
official Lean kernel path and nanoda only.

## Production approval boundary

Repository implementation, tests, PRs, merges, ordinary CI, and automatic
disabled-state deployments are autonomous inside the allowlisted LeanEval
repository family. Stop for explicit maintainer approval before:

- changing AWS, Cloudflare, DNS, OAuth Apps, GitHub Apps, credentials, deploy
  keys, rulesets, or protected environments;
- enabling production intake, replay, publication, or public lifecycle APIs;
- acting in a repository outside the allowlist; or
- expanding the completion-plan scope.

The launch go/no-go packet and capability-enable sequence are maintained in
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
