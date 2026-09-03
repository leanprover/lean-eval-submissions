# LeanEval submissions rollout runbook

Status: **active repository companion**

Scope authority: [LeanEval completion plan][completion-plan]

Cross-repository checklist: [LeanEval execution runbook][execution-runbook]

[completion-plan]: https://github.com/leanprover/lean-eval/blob/main/docs/overhaul-completion-plan.md
[execution-runbook]: https://github.com/leanprover/lean-eval/blob/main/docs/overhaul-execution-runbook.md

Use this file for `lean-eval-submissions` operations only. The completion plan
decides scope and definition of done; the cross-repository runbook decides
phase order and status. Update current values in place. Do not add run logs,
failed-attempt narratives, or evidence tables.

Never put token values, private keys, recovery material, source bytes, or OAuth
client secrets in documentation, issues, pull requests, logs, or artifacts.

## 1. Current posture

The deployed production release at
`b6f8c8834213a26a19ba1e8c7440db30ad0c05f2` tracks:

- durable production intake;
- staging and production general replay disabled;
- historical-public replay disabled;
- production acceptance endpoints disabled;
- the six reviewed production lifecycle and publication-opt-in APIs enabled;
- exactly `kim-em` / GitHub user `477956` in both production maintainer lists;
- staging intake and every staging lifecycle API all-false with empty
  maintainer lists;
- model consolidation and publication opt-out disabled in both environments;
- both historical controller variables absent after one successful
  non-replenishing migrated-envelope private canary, with the bounded drain
  disabled; and
- automatic release publication enabled in `leanprover/lean-eval-releases`.

The launch-readiness packet is `GO`. The production canary is terminal and its
one-way publication opt-in is scheduled for `2026-11-02T03:50:01.002Z`.
The retained historical baseline is promoted in production State at
`76b3b3e54f4be69161a00cd81576a58df8eae815`, tree
`e196521b812a0942eea9d11a8bcb2d7569728d50`; the append-only head may
advance during ordinary intake. The canary fixture branch is deleted and the
temporary source repository is removed from both App selections. The exact
production pause and ordered release/lifecycle/intake restore are complete.
Server-primary presentation is live. The overlap began
`2026-09-02T06:57:10Z`, and issue intake remains available through no earlier
than `2026-09-30T06:57:10Z`.

Verify live state before relying on it:

```sh
tmp_dir=$(mktemp -d)
python3 scripts/monitor_cloudflare_health.py \
  --intake-config server/wrangler.jsonc \
  --replay-config server/wrangler.replay.jsonc \
  --output "$tmp_dir/health.json"
python3 -m json.tool "$tmp_dir/health.json"
```

Remove the temporary directory after inspection. The monitor compares live
health with the checked-out tracked configuration. A ready report must bind all
four public Worker health endpoints to one full
deployed commit and the tracked lifecycle configuration. Also inspect the latest
protected deployment and the canonical readiness issue; endpoint health alone
does not prove that a rollout is not stuck.

## 2. Ordinary protected deployment

[`deploy-worker.yml`](../.github/workflows/deploy-worker.yml) is the normal
runtime deployment path. It:

1. runs types, lint, tests, dependency audit, and Wrangler dry-run checks;
2. enters the reviewer-gated `submission-dispatch-promotion` environment and
   creates or verifies `lean-eval-dispatch/<commit>`;
3. deploys and verifies staging replay, broker, and intake components;
4. runs the staging promotion canary; and
5. deploys and verifies production against the exact tracked intake and
   lifecycle state.

The dispatch-tag ruleset rejects tag update and deletion. Never move or reuse a
dispatch tag. Workflow-only changes use
[`promote-workflow-dispatch-ref.yml`](../.github/workflows/promote-workflow-dispatch-ref.yml)
when its path classification permits; it cannot deploy Workers.

An ordinary merge may trigger a disabled-state deployment. Enter its existing
promotion environment only after the reviewed change is proved unable to
enable a production capability. Environment or ruleset changes are covered by
standing authorization when in scope, but still require an exact change packet,
rollback, and readback.

## 3. Staging intake

Staging intake is changed only by
[`set-staging-intake.yml`](../.github/workflows/set-staging-intake.yml):

1. read the exact deployed staging commit from structured health;
2. select its immutable `lean-eval-dispatch/<commit>` tag;
3. provide the same full commit and the requested `enabled` or `disabled`
   state;
4. verify the resulting structured health; and
5. return staging to disabled after the bounded exercise.

Do not select an old tag merely because it exists. The selected workflow copy
must enforce exact tag/commit/live-version equality. This workflow cannot
target production.

## 4. Production intake

Production has no free-form intake toggle. Launch uses a single-purpose change
to the tracked production `INTAKE_ENABLED` value and its focused expectation.
The deployment controller first proves the exact disabled version and
dependencies, then creates a one-use State-bound lease. The Worker enforces the
lease expiry independently of Actions. Durable enablement is the last step and
occurs only after the lease, health, and protected-State checks pass.

Enabling production intake requires the completed launch readiness packet
described in the cross-repository runbook. Standing authorization covers the
bounded enablement; do not combine it with refactoring, documentation cleanup,
replay expansion, or other feature changes.

Emergency pause uses
[`intake-disable-recovery.yml`](../.github/workflows/intake-disable-recovery.yml).
It can only deploy and verify the all-false launch state: intake, every public
lifecycle family, model consolidation, and the promotion canary are disabled,
and both maintainer arrays are empty. If an enablement workflow fails or is
cancelled, verify public health and run this disable-only recovery before any
later production change.

## 5. Lifecycle API gates

The launch families are configured in `server/wrangler.jsonc`. Before
production enablement, verify that the candidate commit provides an independent
gate for each:

- metadata backfill;
- repair/retraction requests;
- maintainer decisions;
- model aliases and renaming; and
- one-way publication opt-in. The reverse transition remains disabled.

The bounded staging lifecycle exercise is complete. Its temporary controller,
watchdog, fixture, operator driver, and focused tests are retired. Staging
intake and lifecycle APIs are all disabled with empty maintainer lists; model
consolidation and the reverse publication transition remain disabled. The
temporary staging and production-canary source branches are absent, and their
repository has been removed from both App selections. Repository tests retain
coverage of the live lifecycle authorization and fail-closed behavior. Use the
ordinary deployment and disable-only recovery paths for future maintenance; do
not restore a dedicated acceptance harness.

Production uses no free-form lifecycle toggle. The six launch flags and the two
closed maintainer arrays in `server/wrangler.jsonc` form one reviewed rollout
state. [`worker_lifecycle_configuration.py`](../scripts/worker_lifecycle_configuration.py)
requires all six launch flags to be identical, requires exactly one canonical
maintainer identity per maintainer family when enabled and none when disabled,
and always rejects model-consolidation enablement. The normal protected
deployment controller binds this state to the immutable dispatch tag, enters
`cloudflare-production`, and verifies every effective public health field. The
approved lifecycle state and durable intake are deployed at
`b6f8c8834213a26a19ba1e8c7440db30ad0c05f2`.

The same disable-only recovery used for intake also returns every lifecycle
gate to false. It validates the exact recovered Worker version against explicit
all-false bindings and then verifies all effective health fields. Its arming
decision comes from the exact 100%-active production version metadata, not the
tracked desired state. Public health is optional before mutation and mandatory
after it; unavailable or invalid pre-mutation health conservatively arms the
recovery. Configuration drift, a stray effective intake lease, and a failed
deployment that broke health therefore remain recoverable. It cannot enable a
capability.

## 6. Archive and release boundary

Every new server submission must complete archive persistence and the
authenticated `archive.completed` State callback before evaluation starts. The
sidecar is schema version 3 with a fresh per-submission envelope, strict
submission/digest binding, and provider-neutral adapter fields.

Current authority boundaries and environment variables are recorded in
[`../INFRASTRUCTURE.md`](../INFRASTRUCTURE.md). The staging release trust and
credentialed reconstruction boundary are qualified, as is the connected
production archive Wrap-only boundary. The production release role trusts its
exact repository/environment subject, and its pre-enablement
publication-disabled controller, audit-read, and OIDC preflights passed without
decrypting or publishing production source.

The qualified staging release boundary is limited to:

- exact one-submission authority;
- consume-before-unwrap and identical reuse refusal;
- removal of AWS authority before reconstruction execution;
- source allowlisting;
- no plaintext artifact or public log content;
- no State, Results, release, or source-repository mutation; and
- cleanup and revocation of temporary authority.

## 7. Exact-version staging rehearsal

The bounded staging acceptance, production canary, exact production pause, and
ordered restore are complete. Rerun a functional case only if its
implementation path changes or current checks expose drift. Do not rerun broad
functional or historical matrices merely to refresh timestamps.

The compact production go/no-go record is
[`production-launch-readiness.md`](production-launch-readiness.md). Keep its
binding, gate result, and finalization fields current; do not duplicate them as
a run history here.

## 8. Historical completion

The final cutoff and replay process use the canonical files listed in
[`overhaul-tracker.md`](overhaul-tracker.md). Preserve exact source, benchmark,
toolchain, comparator, lean4export, official Lean, and nanoda pins. Never
silently substitute a newer source or toolchain.

Classify each accepted Result as public-source replayable,
private-archive replayable, or reviewed unavailable. Use bounded retries and
record an explicit terminal outcome. The retained-baseline migration is
complete: all 439 recoverable archives have bound schema-version-3 envelopes;
its audit checkpoint is `d73132415738b0d82c99fd43f630804fe996e342`, tree
`48c24fc428eea77d7d9320133fd978f8c7b6abfc`; and the transient installed
identity and review branch are absent. The retained State batch is promoted at
`76b3b3e54f4be69161a00cd81576a58df8eae815`, tree
`e196521b812a0942eea9d11a8bcb2d7569728d50`, with 174 public and 639
private tasks. The production replay role, State/audit keys, and Cloudflare
credential are installed. The non-replenishing migrated-envelope private
canary reached `replay.accepted`, and its cleanup, artifact, resource, scrub,
and Audit checks passed. Both historical controller variables are absent and
general Worker replay remains disabled. Protected State
`d223853a90b37a51d4bbfac30c8213cf78be5778` materializes 174 public and 637
private queued tasks; the bounded two-lane drain remains disabled until explicit
activation.

Retain the migration role, protected environment, one-shot workflow, and
custodian-held legacy key for the separately bound final-cutoff delta. Only
after that delta's audit tree is promoted, read back, and recovery-checked may
the migration lane be removed and the key destroyed; verify that no working
copy remains. Retain v2 replay Decrypt support and the versioned replay records.

## 9. Rollback

Use [`rollback-worker.yml`](../.github/workflows/rollback-worker.yml) with one
reviewed target commit and its broker, replay, and intake version IDs. The
workflow redeploys exact target code while retaining current secret values; it
does not activate historical Cloudflare versions directly. It restores the
replay container from the exact reviewed image digest and leaves intake
disabled. Before mutation it independently proves that protected State is the
reviewed contract or a descendant with unchanged guarded roots and schema.
After restoring target intake, require its authenticated readiness proof before
changing broker or replay.

The current coherent rollback unit is:

- commit `b6f8c8834213a26a19ba1e8c7440db30ad0c05f2`;
- intake `98e1d29e-aa81-4fa5-b095-ac2261d7f9a0`;
- replay `8dabd811-9e81-4a37-95c2-5290b07fbabb`; and
- broker `30d025fd-aa30-40d5-9cbb-a1762fc99725`.

The rollback controller redeploys this target with intake disabled.

Rollback never rewrites State, Results, releases, AWS resources, credentials,
or GitHub repository history. If the multi-component deploy is interrupted,
keep intake paused and rerun the disable-only recovery or forward-deploy one
coherent reviewed unit. Never mix target commits.

## 10. Monitoring and reconciliation

[`lifecycle-readiness-monitor.yml`](../.github/workflows/lifecycle-readiness-monitor.yml)
checks intake, every lifecycle gate, model consolidation, general and
historical replay, and the remaining public endpoint contract against tracked
configuration, then binds that state to a recent successful protected
deployment. A queued/running rollout is suppressed only for its bounded grace
period; an older rollout becomes `deployment_rollout_stuck`. The bot-owned
canonical incident is `lean-eval-submissions#1310`.

At least quarterly and after each infrastructure change:

1. reconcile Worker names, versions, container digest, routes, and effective
   feature states;
2. reconcile GitHub environments, secret names, credential scopes, rulesets,
   and rotation deadlines;
3. reconcile State heads and contract pins;
4. reconcile AWS stack outputs, OIDC subjects, roles, aliases, KMS rotation,
   and one-use tables; and
5. update current values in `INFRASTRUCTURE.md` in place.

## 11. Standing authorization boundary

Standing maintainer authorization covers every remaining in-scope operation,
including AWS, Cloudflare, DNS/OAuth/App, credentials, rulesets and protected
environments, production enablement, canonical data, issue retirement,
announcements, and external non-PR mutations. Complete the applicable exact
packet with targets, immutable inputs, impact, read-only preconditions,
rollback, and post-change verification; do not request repeated permission.
Authenticated maintainer execution remains an operator handoff.

Stop for exact approval only before opening, updating, or merging an external
repository pull request, posting on Zulip, commenting or reviewing another
person's pull request, or expanding the completion-plan scope.
