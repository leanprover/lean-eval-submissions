# lean-eval-submissions

The submission pipeline and the stored results for the
[lean-eval](https://github.com/leanprover/lean-eval) benchmark.

This repository owns two things:

- **The submission process** — the issue intake, the `submission`
  workflow that fetches a submission, evaluates it with
  [comparator](https://github.com/leanprover/comparator), and records the
  outcome, and the reconciler that catches stranded submission issues.
- **The results store** — `results/<github-login>.json`, the append-only
  public log of solved problems.

The benchmark problem set, the `lean-eval` CLI, and the comparator/landrun
security model live in [`leanprover/lean-eval`](https://github.com/leanprover/lean-eval).
The public leaderboard that renders these results is
[`leanprover/lean-eval-leaderboard`](https://github.com/leanprover/lean-eval-leaderboard)
(**[view it →](https://lean-lang.org/eval/)**).

## Submitting a solution

Open a [**Submit benchmark solution**](https://github.com/leanprover/lean-eval-submissions/issues/new?template=submit.yml)
issue. You point it at any content that contains at least one
`lakefile.toml` whose `name` matches a benchmark problem id with a
`Submission.lean` alongside it — a generated workspace, a fork of
`leanprover/lean-eval` with changes under `generated/`, a repo with
several workspaces, or a public gist. The CI walks the content and tries
every match.

Only `Submission.lean` and files under `Submission/` are read. Nothing
else from your submission is inspected or published — only the set of
solved problem ids plus the metadata you enter on the form.

If your submission lives in a **private** repository, install the
`lean-eval-bot` GitHub App on it so the CI can clone it:
**<https://github.com/apps/lean-eval-bot>**.

## Results store

`results/` holds **machine-written** artifacts produced by the submission
CI. Do not edit them by hand.

```
results/
  <github-login>.json
```

One file per submitter; filenames use the lowercased GitHub login. Users
without a successful submission have no file.

Successes are **sticky**: once a `(user, model, problem)` triple is
recorded it is never modified or removed, even if a later submission from
the same user no longer proves it.

### Record schema (v1)

```json
{
  "schema_version": 1,
  "user": "kim-em",
  "solved": {
    "Claude Opus 4.7": {
      "two_plus_two": {
        "solved_at": "2026-05-01T03:16:18Z",
        "benchmark_commit": "953d54a7af5038566775507761e48e365e7feb3b",
        "submission_kind": "gist",
        "submission_repo": "kim-em/22bad2dccd67bcca0df87c01d072ef39",
        "submission_ref": "567b8d1feebbc6ccbb1f8ebb0a7bbcf5e914f135",
        "submission_public": true,
        "issue_number": 45,
        "production_description": "..."
      }
    }
  }
}
```

| Field | Type | Description |
| --- | --- | --- |
| `schema_version` | integer | Currently `1`. |
| `user` | string | GitHub login, original case preserved. |
| `solved` | object | Map from model name to per-problem records. Never empty. |

The keys of `solved` are free-form model identifiers from the submission
form. Each value maps `<problem_id>` to a record:

| Field | Type | Description |
| --- | --- | --- |
| `solved_at` | string | ISO 8601 UTC timestamp the record was first written. |
| `benchmark_commit` | string | 40-char SHA of the `leanprover/lean-eval` commit evaluated against. |
| `submission_kind` | string | `github_repo` or `gist`. |
| `submission_repo` | string | `owner/repo` for a repository, `user/gist-id` for a gist. |
| `submission_ref` | string | 40-char SHA pinning the submission at evaluation time. |
| `submission_public` | boolean | Whether the submission source was public at evaluation time. |
| `issue_number` | integer | The `leanprover/lean-eval-submissions` issue that triggered the evaluation. |
| `production_description` | string \| absent | Optional free-form description from the form. |

> **`issue_number` provenance.** Records written by this repository refer
> to `leanprover/lean-eval-submissions` issues. Records dated before the
> submission pipeline moved here refer to `leanprover/lean-eval` issues.

### Write semantics

When the submission CI records a successful submission:

1. It reads `results/<login>.json`, or starts from an empty `solved` map.
2. The submission carries one model name; that is the bucket key.
3. For each problem that passed: if `solved[<model>][<problem_id>]`
   already exists, do nothing (sticky no-op); otherwise add a record.
4. If at least one new record was added, the CI commits and pushes;
   otherwise it makes no commit.

Breaking schema changes bump `schema_version`; consumers should refuse a
file whose `schema_version` they do not know.

## How the pipeline fits together

```
submission issue on lean-eval-submissions
  → submission.yml: checkout leanprover/lean-eval (problem set + probes), evaluate
  → write results/<login>.json here, push
  → repository_dispatch results-advanced → lean-eval-leaderboard redeploys
```

`submission-reconciler.yml` is an hourly safety net: it closes submission
issues that never received a bot comment (workflow disabled, runner died,
etc.).

## Operator notes

- Secrets, GitHub Apps, and branch protection: [`docs/ci-secrets.md`](docs/ci-secrets.md).
- Security model / threat analysis: [`SECURITY.md`](SECURITY.md).
- `ci.yml` runs the Python test suite, `actionlint`, the workflow-pin
  audit, and `tests/test_submission_workflow.py` (a structural guard on
  `submission.yml`'s security-critical shape).
