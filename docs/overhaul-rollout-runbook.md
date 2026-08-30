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

Keep the not-yet-deployed candidate distinct from live service state.

The launch candidate tracks:

- production intake disabled;
- staging and production general replay disabled;
- historical-public replay disabled;
- production acceptance endpoints disabled;
- production result-owner, amendment-owner, amendment-maintainer,
  model-identity-owner, model-identity-maintainer, and release-opt-out APIs
  enabled;
- exactly `kim-em` / GitHub user `477956` in both production maintainer lists;
- staging intake and every staging lifecycle API all-false with empty
  maintainer lists;
- model consolidation disabled in both environments; and
- release publication disabled in `leanprover/lean-eval-releases`.

It has not been deployed and the launch-readiness packet remains `NO-GO`. The
live staging and production Workers are still deployed at
`30bc92b3d46bd2a3ba1788433264fdd70ae3c74e`: production intake and every public
production lifecycle API are effectively disabled, both live production
maintainer lists are empty, and live staging intake and lifecycle APIs remain
all-false. The bounded staging acceptance and promotion-canary exceptions keep
their separately documented staging-only posture.

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
health with the checked-out tracked configuration. It is therefore expected to
report a mismatch on this not-yet-deployed launch candidate; use the exact
deployed commit when verifying the current all-false live baseline. After
deployment, a ready report must bind all four public Worker health endpoints to
one full deployed commit and the tracked candidate configuration. Also inspect
the latest protected deployment and the canonical readiness issue; endpoint
health alone does not prove that a rollout is not stuck.

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
- release opt-out.

Before completing the production launch packet, run one owner/maintainer
success and one authorization or validation denial for each family, then return
every gate to disabled and verify effective public health. Keep model
consolidation disabled.

The bounded staging exercise uses
[`set-staging-lifecycle-smoke.yml`](../.github/workflows/set-staging-lifecycle-smoke.yml)
from the immutable `lean-eval-dispatch/<commit>` tag for the exact live staging
commit. The enabled dispatch requires closed one-member result-amendment and
model-identity maintainer arrays and turns on only staging intake and the five
launch families; model consolidation remains disabled. The reviewed cases and
immutable source inputs are in
[`staging-lifecycle-smoke-v1.json`](../configuration/staging-lifecycle-smoke-v1.json).
Values listed under `runtime_allocated` are recorded from the live responses
and cannot be fixed in advance.

When the enabled state converges, the controller itself makes the fixture's
single unauthenticated browser grant request and requires the exact `401`
`authentication_failed` response. It supplies neither a cookie nor an
authorization header, and a mismatch invokes the same all-false recovery as
any other failed enabled-state check.

The metadata-backfill denial is not an empty-patch validation case. The
authenticated staging operator sends the fixture's valid, nonempty patch for
the stable `eohjelle`-owned Result named there and requires the owner-hiding
`404 not_found` response. A denial must not create a State event.

For the authenticated non-maintainer cases, first enable with the fixture's
closed denial profiles and make only the two expected `404` decision requests.
Then re-dispatch the same exact commit with the success profiles before the
owner and maintainer success cases. The headless success additionally requires
the operator to create the returned secret-Gist proof and exact
`lean-eval/<submission-id>` source tag. The source commit lives on temporary
non-default branch `staging-source-fixture-v1` in the allowlisted private
`leanprover/lean-eval-state-staging` repository, so the tag write is in-family.
Standing authorization covers the external secret-Gist compare-and-swap write,
limited to the exact target-bound proof and cleanup described below; the
authenticated browser action remains an operator handoff. The source-mismatch
denial is checked before either external lookup and needs neither mutation.

Restore the same exact commit by dispatching the workflow with `state=disabled`,
both maintainer arrays equal to `[]`, and the explicit confirmation selected.
That single disable operation returns intake and every launch family to false,
keeps model consolidation false, and verifies each effective health field. A
failure after the workflow arms the mutation invokes the same all-false deploy
automatically for an ordinary step failure. Cancellation after deployment may
prevent that recovery step: immediately re-dispatch the same exact tag with
`state=disabled` and both maintainer arrays equal to `[]`, then verify every
launch field is false in public health. Do not use the enabled state outside
the packet-bound smoke.

The final exercise uses the temporary
[`bounded-staging-lifecycle-watchdog.yml`](../.github/workflows/bounded-staging-lifecycle-watchdog.yml)
and [`run_bounded_staging_lifecycle.py`](../scripts/run_bounded_staging_lifecycle.py).
They are a bounded operator aid, not a qualification harness or deployment
controller, and must be deleted with the staging fixture after one accepted
run. The exact operator sequence is:

The release reconstruction dispatch targets protected release `main` and sends
the fixture's exact `expected_release_commit`. The release workflow itself
requires protected `refs/heads/main` and rejects unless `github.sha` equals that
input. Preflight also requires release `main` to equal the same reviewed commit;
there is no movable or temporary release tag authority.

1. run the driver's `preflight` command; it is read-only and must report zero
   writes;
2. start the exact-tag watchdog while public health is still all-false; it
   verifies that state, prepares the recovery deployment, arms recovery, and
   only then waits for enablement for at most 15 minutes;
3. after the watchdog reports that recovery is armed, dispatch the exact
   immutable tag through `set-staging-lifecycle-smoke.yml` with the fixture's
   two denial maintainer arrays and `state=enabled`;
4. make one ordinary browser submission, wait for its Result, and click the
   visible release opt-out control; no browser cookie, token, or DevTools value
   is copied;
5. run the driver with only the visible browser submission ID, secret gist ID,
   and exact staging commit. It derives the browser Result through the
   same-owner agent session and accepts no Result ID from the operator;
6. when the driver pauses after receiving the headless challenge, verify the
   displayed nonsecret Gist/file and allowlisted repository/tag/commit targets,
   then type the target-bound confirmation it displays. The maintainer's
   standing authorization covers only this non-PR temporary Gist proof, the
   in-family tag, and their exact cleanup; a different external target remains
   a hard stop. Let the driver make only those writes and complete the headless,
   denial, lifecycle, State, Results, redacted-projection, scheduling, uniquely
   named publication-disabled reconstruction from the fixture's guarded exact
   release commit, and disabled-route checks. The release run is accepted only
   when its untruncated job inventory is exactly `authorize-manual`,
   `prepare-one`, and `unwrap-one`, all completed successfully; workflow-level
   success alone is insufficient. Once
   headless evaluation is terminal, it restores the gist file to its exact
   prior content (or absence) and deletes the generated exact tag; and
7. verify the driver's prompt disable dispatch and the independent watchdog's
   eventual all-false health result. If either is interrupted, immediately use
   the existing exact-tag disabled dispatch and verify public health.

Before the packet-bound proof write, the driver keeps the signed challenge only
in ordinary process memory. During the exact mutation, its only other
representations are the named secret Gist proof and a mode-`0700` Git checkout
on verified tmpfs; the driver removes the checkout synchronously, clears request
copies after submission, and retains one comparison value in memory only until
exact cleanup. This is not a hardened enclave. Run it only on the trusted
operator host, with core dumps and untrusted process
inspection disabled; the host's swap and runtime may still copy process memory.
The challenge must never be placed in workflow inputs, ordinary disk, logs,
summaries, artifacts, or documentation. If
interruption prevents exact fixture cleanup, the driver prints only the
nonsecret rollback targets; do not remove the tag before headless evaluation is
terminal. Its captured prior gist content remains only in process memory. Any
repository, source commit, gist owner/visibility, live staging commit,
canonical fixture digest, or immutable-tag mismatch is a hard stop. The driver
may dispatch only the existing in-family publication-disabled staging
reconstruction; it cannot publish a release.

Once the headless submission POST begins, a missing or malformed response is an
unknown acceptance outcome: State acceptance and dispatch may already have
completed. In that case the driver must retain both the exact proof file and tag
and print only their nonsecret recovery targets. Restore/delete them only after
the exact submission has been reconciled to a terminal archive/evaluation
state; transport failure alone never proves cleanup safe.

Cleanup is deliberately ownership-bounded. The driver restores only the gist
state it captured and deletes the source tag only after an exact successful
create response proved that this run created it. Gist write and restoration
strictly discover the Gist's symbolic HEAD as `refs/heads/main` or the legacy
`refs/heads/master`, bind its advertised commit to the API snapshot, and use
Git compare-and-swap on that unchanged ref with exact `--force-with-lease`
heads. The only checkout and object database live in a mode-`0700` directory on
verified `/dev/shm` tmpfs; the Git subprocess environment is closed and omits
all debug/trace variables and unrelated credentials. The driver accepts at
most 16 complete Gist files and 1 MiB of current content, fetches only the exact
branch with depth one for the write and depth two for restoration, and applies
a timeout to every Git operation. Restoration first proves that the current
head and file are still this run's exact challenge commit, then atomically
returns the ref to the captured prior head. It never uses an undocumented
conditional HTTP PATCH and never overwrites an intervening edit or deletion.
If a source-tag create response is lost and a tag appears, its
ownership is ambiguous: the driver restores the proved Gist change, refuses to
delete the tag, and reports only the exact nonsecret rollback target for a
fresh reviewed recovery decision.

The final disabled-route assertions are transient response checks. Intake must
return `503 intake_disabled`; every launch lifecycle gate—including legacy
result-owner, result-amendment owner and maintainer, model-identity owner and
maintainer, and release opt-out—must return the public owner-hiding `404
not_found`. Model consolidation stays excluded. For lifecycle probes the driver
also proves its generated idempotency event is absent from State. Do not create
a State event, durable evidence record, artifact, or run-history document for
these denials.

This lifecycle driver does not verify the public entry page. Separately perform
the browser UI regression check at `lean-lang.org/eval/submit`: problem text,
pre-filled form values, sign-in feedback, preparing spinner, submission status,
and the visible opt-out control must all work without DevTools.

After the single accepted run and verified all-false recovery, retirement is a
required source change, not optional cleanup. Delete this exact inventory:

- `.github/workflows/bounded-staging-lifecycle-watchdog.yml`;
- `.github/workflows/set-staging-lifecycle-smoke.yml`;
- `configuration/staging-lifecycle-smoke-v1.json`;
- `scripts/run_bounded_staging_lifecycle.py`;
- `tests/test_bounded_staging_lifecycle_acceptance.py`; and
- `tests/test_staging_lifecycle_smoke.py`.

After every dependent archive and evaluation is terminal, also delete temporary
branch `staging-source-fixture-v1` from
`leanprover/lean-eval-state-staging` and remove that repository from the
selected installations of both read-only source Apps. Never remove the branch
while a generated source tag or dependent run remains.

Remove this bounded-acceptance subsection and any tests or workflow inventories
that reference those paths in the same retirement change. Retain no replacement
qualification harness, run artifact, or history document.

Production uses no free-form lifecycle toggle. The six launch flags and the two
closed maintainer arrays in `server/wrangler.jsonc` form one reviewed rollout
state. [`worker_lifecycle_configuration.py`](../scripts/worker_lifecycle_configuration.py)
requires all six launch flags to be identical, requires exactly one canonical
maintainer identity per maintainer family when enabled and none when disabled,
and always rejects model-consolidation enablement. The normal protected
deployment controller reads that state, binds it to the immutable dispatch tag,
enters `cloudflare-production`, and verifies every effective public health
field. This branch is the single-purpose lifecycle configuration candidate.
Do not deploy it until the production launch packet is complete and still
reads `GO`; do not combine its deployment with intake enablement, release
publication, refactoring, or unrelated implementation changes.

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
production archive Wrap-only boundary. The remaining production release-trust
repair is covered by standing authorization but must follow the exact
operator-handoff procedure in
[`aws-release-production-trust-repair.md`](aws-release-production-trust-repair.md)
without decrypting or publishing production source.

The qualified staging release boundary is limited to:

- exact one-submission authority;
- consume-before-unwrap and identical reuse refusal;
- removal of AWS authority before reconstruction execution;
- source allowlisting;
- no plaintext artifact or public log content;
- no State, Results, release, or source-repository mutation; and
- cleanup and revocation of temporary authority.

## 7. Exact-version staging rehearsal

Use synthetic private repositories owned for staging. Against the exact
candidate commits:

1. submit once through browser OAuth;
2. submit once through the source-bound headless flow;
3. include one deliberate invalid or unauthorized request;
4. prove archive-before-evaluation and schema-version-3 binding;
5. verify terminal Result and append-only State events;
6. verify release scheduling and the redacted leaderboard projection;
7. reconstruct one accepted archive with publication disabled; and
8. exercise the reviewed disable/rollback path and validate staging State.

Do not rerun broad historical matrices to refresh timestamps.

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
record an explicit terminal outcome. Private legacy-envelope migration requires
its exact immutable execution packet and credential-custodian operator handoff.
Standing authorization covers the bounded infrastructure, credential, and
canonical writes; rewrap the per-submission key without changing archive
ciphertext or stable IDs, then remove temporary authority and plaintext.

## 9. Rollback

Use [`rollback-worker.yml`](../.github/workflows/rollback-worker.yml) with one
reviewed target commit and its broker, replay, and intake version IDs. The
workflow redeploys exact target code while retaining current secret values; it
does not activate historical Cloudflare versions directly. It restores the
replay container from the exact reviewed image digest and leaves intake
disabled.

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
