# Security model: the lean-eval submission pipeline

The planned encrypted-source recovery path has a separate, currently disabled
security contract in [`docs/replay.md`](docs/replay.md). It must not weaken the
same-job, no-plaintext-artifact invariants documented below.

The Wave 2 public-source planner, credential-free historical smoke, and
disposable-VM handoff are specified in
[`docs/replay-orchestrator.md`](docs/replay-orchestrator.md). The smoke has only
contents-read permission, restores public inputs at exact commits, removes all
credential-bearing checkout/component Git metadata, and uploads only
source-free JSON after the sandbox probes. The
authoritative execution request admits no credentials or ambient environment
and disables network access before untrusted Lean runs; structural tests guard
those properties. Private replay remains nonterminally blocked pending D6; it
stays queued and does not emit `replay.unavailable`.

This document explains why we believe the lean-eval submission pipeline
is resistant to adversarial submissions, what assumptions it depends on,
and where a future red-teamer should look first.

It covers the **submission pipeline** — authenticated server dispatch,
fetching submission source, the evaluation workflow, and recording results. The
**comparator / landrun sandbox** that actually bounds untrusted Lean —
the Challenge/Submission/Solution architecture, where untrusted code
runs, the trust model for the comparator, the pinned-dependency policy,
and the sandbox probes — lives in the benchmark repository and is
documented there:

> **[`leanprover/lean-eval` → `SECURITY.md`](https://github.com/leanprover/lean-eval/blob/main/SECURITY.md)**

Read both. This pipeline checks out `leanprover/lean-eval` at evaluation
time and runs that repo's probe scripts; the guarantees here are only as
strong as the sandbox guarantees there.

## 1. Threat model

The attacker controls:

- One exact commit in a public/private GitHub repository containing
  `Submission.lean` and any number of
  files under `Submission/**/*.lean`. Nothing else from the submission
  source is consumed.
- The declared model, production description, publication choice, and other
  bounded fields accepted by the submission service.

The attacker does not control:

- `Challenge.lean`, `Solution.lean`, `lakefile.toml`, `lean-toolchain`,
  `config.json`, `WorkspaceTest.lean` in the generated workspace. These
  are taken from a pristine `generated/<id>/` checkout of
  `leanprover/lean-eval` each run.
- Any pinned upstream commit (landrun, lean4export, comparator,
  GitHub Actions). Pin policy and the bump procedure are documented in
  `leanprover/lean-eval`'s `SECURITY.md`.
- Which `leanprover/lean-eval` commit is evaluated against: the
  `evaluate` job resolves `leanprover/lean-eval@main` HEAD and records
  the resolved SHA as `benchmark_commit`.

The goal we resist: **a submitter receiving credit on the leaderboard
for a theorem they have not actually proved.**

The submission service validates the authenticated owner, exact commit,
catalog identity, publication choice, and bounded metadata before dispatching
the immutable workflow tag. The workflow repeats the exact-ref and input
checks. The proof boundary remains the pristine benchmark overlay and
comparator's landrun sandbox, not the intake service.

## 2. Submission confidentiality

**Submission confidentiality is best-effort, not a guarantee.** Private
submissions (those stored in a private GitHub repo readable only via
the `lean-eval-bot` App) are evaluated without uploading their source as
a workflow artifact, so the source is not exposed to anyone authenticated
against the GitHub Actions API. Confidentiality of the source — and of
the App installation token used to clone it — depends on several
properties of `submission.yml`'s structure that we do not actively probe:

- **fetch and evaluate share one job**, and private source must never be
  uploaded as an artifact or cross a runner boundary. Do not split these jobs.
  (`tests/test_submission_workflow.py` asserts they stay merged.)
- `APP_INSTALLATION_TOKEN` is scoped to the env of the single
  `Fetch submission` step.
- `fetch_submission.py` strips `.git/` from the cloned source before
  tarring, because `clone_url_for` embeds the installation token in the
  `origin` remote URL and `git remote add` persists that URL into
  `.git/config` (regression test: `FetchSubmissionTarballHygieneTests`).
- Both checkouts (`lean-eval-submissions` and `leanprover/lean-eval`)
  use `persist-credentials: false`, and `.git` is stripped from both
  before any untrusted Lean runs. Comparator's landrun policy is
  `--ro /`, so anything left on the runner under a path the sandbox can
  stat is readable by the untrusted Lean elaborator.

`tests/test_submission_workflow.py` is the structural guard for the
first, third, and fourth bullets — a workflow refactor that regresses
them fails CI.

The pinned `actions/create-github-app-token` v3.2.0 writes the token to
`$RUNNER_TEMP/_runner_file_commands/{set_output,save_state}_<uuid>`
during the mint step, but actions/runner's `FileCommandManager` deletes
the previous step's files at the start of every step, so by the time
untrusted Lean runs in `evaluate_submission.py` those files have been
deleted many steps earlier. Deeper shared-host paths that apply to any
secret on a GitHub-hosted runner (e.g. reading
`/proc/<runner-worker-pid>/environ` or attaching ptrace to the worker)
are partially mitigated by Ubuntu's `kernel.yama.ptrace_scope=1` but are
not something we actively probe. Submitters who require confidentiality
should audit the workflow themselves before relying on this.

**Audit retention.** Every successfully fetched submission has its compressed
source tarball (≤ 10 MiB) encrypted to one fresh age identity and pushed to
the private `leanprover/lean-eval-audit` repo for indefinite retention. The
identity is wrapped into a provider-neutral, submission-bound envelope through
the production KMS adapter; it is never persisted in plaintext. The unencrypted
sidecar JSON records the UUID, submitter, repo+ref, model, provenance, envelope,
and integrity digests. Historical issue-intake archives retain their earlier
shared-recipient format. Current archives are durably committed before an
evaluation verdict exists. This is disclosed by the submission service and the
"Audit archive" section of the README. See
[`docs/audit-archive.md`](docs/audit-archive.md) for the threat model
and key custody story. The `record` job is gated on the `archive` job
succeeding, so a recorded leaderboard entry always implies a durable
encrypted archive of the source.

Server intake keys the archive by its canonical UUIDv7 under
`archives/<prefix>/<uuid>.tar.age`.
Before State may receive `archive.completed`, the archiver emits an immutable
repository/commit/path/ciphertext-digest locator and verifies the encrypted
bytes at that exact commit.

The trusted archive job now independently fetches the exact source commit and
persists the encrypted archive before evaluation starts. The evaluation job
then performs its own exact-commit fetch and verifies the archive job's frozen
metadata digest before keeping fetch and evaluation co-located. No plaintext
or ciphertext transport artifact crosses jobs. Only the archive job may gain
the Encrypt-only OIDC role;
the evaluation job must never gain `id-token: write`, AWS credentials, a
wrapped identity, or KMS/DynamoDB/Lambda authority.

## 3. The two-checkout evaluation workflow

`submission.yml`'s `evaluate` job is the only place untrusted submitter
Lean is elaborated, and it is elaborated only inside comparator's landrun
sandbox (see `leanprover/lean-eval`'s `SECURITY.md` §3 for the full
"where untrusted code runs" table). The job:

1. Checks out this repo (the pipeline scripts) and `leanprover/lean-eval`
   at the exact commit frozen by the preceding archive job, both with
   `persist-credentials: false`.
2. Verifies `benchmark_commit` and toolchain against the archive outputs.
3. Independently fetches the submission with a step-scoped `lean-eval-bot`
   token and verifies the deterministic metadata digest against archive.
4. Strips `.git` from both checkouts before untrusted Lean runs.
5. Builds landrun / lean4export / comparator / the `lean-eval` CLI.
6. Fetches Mathlib's independent cache, but does not restore from or save
   to the repository's GitHub Actions cache. The evaluate job deliberately
   omits `actions: write`, so artifacts from a runner that executes
   untrusted submitter code never enter a shared mutable cache scope.
7. Runs the sandbox-engaged and env-allowlist probes **from the
   `leanprover/lean-eval` checkout** (`lean-eval/scripts/...`). Those
   probes live in the benchmark repo because they guard against sandbox
   regressions introduced by *benchmark-repo* changes; this pipeline
   re-runs them as a per-submission pre-flight gate.
8. Runs `evaluate_submission.py`, which overlays the submission onto a
   pristine `generated/<id>/` workspace and invokes comparator.

The `record` job then writes the result. It uses **two checkouts of this
repo**: a read-only `code/` checkout (pinned to the workflow SHA, supplies
`update_leaderboard.py`) and a writable `results-store/` checkout. The
push-retry loop resets `results-store/` to the selected protected Results
branch (`main` for production/issues, `staging-results` for staging) between attempts;
keeping the script in a separate checkout means the loop cannot reset the
running script out from under itself.

For Worker-originated submissions, archival completion crosses a separate
credential boundary. The archive job uploads only a strict locator/completion
artifact after verifying the ciphertext bytes at the recorded immutable audit
commit. The source-free `archive_state` job alone receives the matching
environment's `LIFECYCLE_CALLBACK_TOKEN`; it sends the completion to the Worker,
which appends the causally linked `archive.completed` event with a
domain-separated SHA-256-derived UUIDv7. Retries therefore target the same
immutable event path even after credential rotation.
The callback token and State credential are absent from the fetch/evaluate and
archive jobs, and the callback endpoint remains available for in-flight jobs
when public intake is disabled.

## 4. Recording validations

- **Schema validation.** `update_leaderboard.py` validates
  `submission-ref` and `benchmark-commit` as 40-char hex SHAs,
  `submission-repo` as `owner/name`, `submission-kind` as `github_repo`
  or `gist`, `--user` as a GitHub login regex, and the statement revision
  frozen into the uncredentialed evaluator's artifact. It accepts results
  schema versions 1 and 2 but validates the complete schema version 2 envelope
  and identifier before writing.
- **Sticky no-op writes.** A result for an already-recorded
  `(user, verbatim model, problem, statement revision)` tuple is a no-op;
  base records only grow. See the README's record-schema section.
- **Push identity.** The `record` job's results push is authored by the
  `lean-eval-recorder` GitHub App, which is the explicit branch-protection
  bypass actor for this repo's `main` (see `docs/ci-secrets.md`). Only
  this workflow holds that App's credentials.
- **Migration serialization.** The results schema version 2 migration uses
  that same App and first
  commits a durable `results-store-writer` lock. Record jobs keep evaluating
  and archiving, then fetch and wait at the credentialed write boundary. The
  lock is removed atomically with the migration commit. See
  `docs/results-schema-v2.md`; do not replace it with a static record-job
  Actions concurrency group, which cancels older pending jobs rather than
  queueing all of them.
- **Leaderboard redeploy.** After a successful production results push the job fires
  a `results-advanced` `repository_dispatch` at
  `leanprover/lean-eval-leaderboard`. Dispatch failure after a successful
  push is surfaced as an operator-actionable workflow warning, not a job
  failure — the data advanced even if the site did not.

## 5. Soft spots — where to look first

Submission-pipeline soft spots. Comparator/sandbox soft spots are in
`leanprover/lean-eval`'s `SECURITY.md` §7.

1. **HTML escaping of freeform fields on the leaderboard.** The `model`
   and `production_description` fields are submitter-controlled text,
   propagated into the results store and rendered by the
   leaderboard site. The renderer should escape, but this has not been
   explicitly probed.
2. **Freeform `model` length.** `update_leaderboard.py` validates
   `production_description` length but not `model`. A pathological
   `model` value cannot grant credit, but can pollute the results JSON.
3. **Public intake can consume CI capacity.** An authenticated user can submit
   a well-formed request through the service and trigger evaluation. Intake
   rate limits, repository Actions limits, and operator moderation remain the
   resource-abuse controls.
4. **Cross-repo benchmark drift.** The pipeline scores against
   `leanprover/lean-eval@main` HEAD while the leaderboard site renders a
   catalog pinned by its own `benchmark-snapshot/`. A result can be
   recorded against a benchmark commit slightly newer than the snapshot;
   the leaderboard's `generate_site_data.py` tolerates result entries
   whose problem id is not yet in the snapshot.
5. **Workflow-structure drift.** The confidentiality argument in §2
   depends on `submission.yml`'s shape. `tests/test_submission_workflow.py`
   guards the load-bearing invariants, but it is a text-level check — a
   refactor that preserves the matched strings while changing behaviour
   could still regress. Review `submission.yml` changes against §2 and §3.

## References

- [`docs/public-replay-resolution.md`](docs/public-replay-resolution.md) — the
  historical issue/run/comment resolver and its contents/issues/Actions
  read-only token boundary.
- [`leanprover/lean-eval` SECURITY.md](https://github.com/leanprover/lean-eval/blob/main/SECURITY.md)
  — the comparator/sandbox security model this pipeline depends on.
- [`leanprover/comparator`](https://github.com/leanprover/comparator) — the verifier.
- `scripts/fetch_submission.py`, `scripts/evaluate_submission.py`, and
  `scripts/update_leaderboard.py` — the pipeline scripts.
- `.github/workflows/submission.yml` and `.github/workflows/server-archive.yml`
  — the pipeline workflows.
- `docs/ci-secrets.md` — the credentials and branch protection this pipeline depends on.
