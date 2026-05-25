# Security model: the lean-eval submission pipeline

This document explains why we believe the lean-eval submission pipeline
is resistant to adversarial submissions, what assumptions it depends on,
and where a future red-teamer should look first.

It covers the **submission pipeline** — issue intake, fetching submission
source, the evaluation workflow, and recording results. The
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

- One submission they file as a GitHub issue: a public/private GitHub
  repo (or public gist) containing `Submission.lean` and any number of
  files under `Submission/**/*.lean`. Nothing else from the submission
  source is consumed.
- The freeform `model` and `production_description` fields in the issue
  body.

The attacker does not control:

- Whether the `submission` label gets applied — that requires triage
  permission on `leanprover/lean-eval-submissions`. The issue form lists
  the label, but GitHub only applies a form's labels for an author who
  has triage permission; for everyone else a maintainer must apply it.
  That is the trust gate.
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

## 2. Submission confidentiality

**Submission confidentiality is best-effort, not a guarantee.** Private
submissions (those filed against a private GitHub repo readable only via
the `lean-eval-bot` App) are evaluated without uploading their source as
a workflow artifact, so the source is not exposed to anyone authenticated
against the GitHub Actions API. Confidentiality of the source — and of
the App installation token used to clone it — depends on several
properties of `submission.yml`'s structure that we do not actively probe:

- **fetch and evaluate share one job**, so the source never crosses a
  runner boundary. An earlier design uploaded the cloned source as a
  `submission-source` artifact for a separate `evaluate` job to pick up;
  on a public repo anyone authenticated can download workflow artifacts,
  which leaked private submissions. Do not re-split these jobs.
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

`actions/create-github-app-token` does write the token to
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

**Audit retention.** Every successfully fetched submission has its
compressed source tarball (≤ 10 MiB) `age`-encrypted to the recipient
list in `.audit/recipients.txt` and pushed to the private
`leanprover/lean-eval-audit` repo for indefinite retention. The
ciphertext is decryptable only by holders of the matching SSH/age
private keys; the unencrypted sidecar JSON records issue, submitter,
repo+ref, model, and the evaluator verdict. This is disclosed to
submitters via the third acknowledgement on the submission Issue Form
and the "Audit archive" section of the README. See
[`docs/audit-archive.md`](docs/audit-archive.md) for the threat model
and key custody story. The `record` job is gated on the `archive` job
succeeding, so a recorded leaderboard entry always implies a durable
encrypted archive of the source.

## 3. The two-checkout evaluation workflow

`submission.yml`'s `evaluate` job is the only place untrusted submitter
Lean is elaborated, and it is elaborated only inside comparator's landrun
sandbox (see `leanprover/lean-eval`'s `SECURITY.md` §3 for the full
"where untrusted code runs" table). The job:

1. Checks out this repo (the pipeline scripts) and `leanprover/lean-eval`
   at `main` (the benchmark) into `lean-eval/`, both with
   `persist-credentials: false`.
2. Resolves `benchmark_commit` from `git -C lean-eval rev-parse HEAD`.
3. Fetches the submission with the step-scoped `lean-eval-bot` token.
4. Strips `.git` from both checkouts.
5. Builds landrun / lean4export / comparator / the `lean-eval` CLI.
6. Runs the sandbox-engaged and env-allowlist probes **from the
   `leanprover/lean-eval` checkout** (`lean-eval/scripts/...`). Those
   probes live in the benchmark repo because they guard against sandbox
   regressions introduced by *benchmark-repo* changes; this pipeline
   re-runs them as a per-submission pre-flight gate.
7. Runs `evaluate_submission.py`, which overlays the submission onto a
   pristine `generated/<id>/` workspace and invokes comparator.

The `record` job then writes the result. It uses **two checkouts of this
repo**: a read-only `code/` checkout (pinned to the workflow SHA, supplies
`update_leaderboard.py`) and a writable `results-store/` checkout. The
push-retry loop resets `results-store/` to `origin/main` between attempts;
keeping the script in a separate checkout means the loop cannot reset the
running script out from under itself.

## 4. Recording validations

- **Schema validation.** `update_leaderboard.py` validates
  `submission-ref` and `benchmark-commit` as 40-char hex SHAs,
  `submission-repo` as `owner/name`, `submission-kind` as `github_repo`
  or `gist`, and `--user` as a GitHub login regex.
- **Sticky no-op writes.** A result for an already-recorded
  `(user, model, problem)` triple is a no-op; the map only grows. See the
  README's record-schema section.
- **Push identity.** The `record` job's results push is authored by the
  `lean-eval-recorder` GitHub App, which is the explicit branch-protection
  bypass actor for this repo's `main` (see `docs/ci-secrets.md`). Only
  this workflow holds that App's credentials.
- **Leaderboard redeploy.** After a successful results push the job fires
  a `results-advanced` `repository_dispatch` at
  `leanprover/lean-eval-leaderboard`. Dispatch failure after a successful
  push is surfaced as an operator-actionable issue comment, not a job
  failure — the data advanced even if the site did not.

## 5. Soft spots — where to look first

Submission-pipeline soft spots. Comparator/sandbox soft spots are in
`leanprover/lean-eval`'s `SECURITY.md` §7.

1. **HTML escaping of freeform fields on the leaderboard.** The `model`
   and `production_description` fields are unbounded freeform text from
   the issue body, propagated into the results store and rendered by the
   leaderboard site. The renderer should escape, but this has not been
   explicitly probed.
2. **Freeform `model` length.** `update_leaderboard.py` validates
   `production_description` length but not `model`. A pathological
   `model` value cannot grant credit, but can pollute the results JSON.
3. **The `submission` label is a triage gate.** Anyone with triage
   access on `leanprover/lean-eval-submissions` can trigger the workflow
   against any submission URL. Repository admin hygiene matters.
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

- [`leanprover/lean-eval` SECURITY.md](https://github.com/leanprover/lean-eval/blob/main/SECURITY.md)
  — the comparator/sandbox security model this pipeline depends on.
- [`leanprover/comparator`](https://github.com/leanprover/comparator) — the verifier.
- `scripts/fetch_submission.py`, `scripts/evaluate_submission.py`,
  `scripts/update_leaderboard.py`, `scripts/reconcile_orphan_submissions.py`
  — the pipeline scripts.
- `.github/workflows/submission.yml`, `.github/workflows/submission-reconciler.yml`
  — the pipeline workflows.
- `docs/ci-secrets.md` — the credentials and branch protection this pipeline depends on.
