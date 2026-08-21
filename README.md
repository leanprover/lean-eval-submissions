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

### Submitting through the GitHub API

API-created issues are supported. Create an issue whose title starts with
`[submission] ` and whose body uses the same rendered Markdown sections as
the [submission Issue Form](.github/ISSUE_TEMPLATE/submit.yml). For example,
`gh issue create --repo leanprover/lean-eval-submissions --title
'[submission] my proof' --body-file submission.md` uses the GitHub API.

The body must include the required `Submission URL`, `Model`, exact-solution
publication fields, and all three checked acknowledgements. Do not depend on
the API request's `labels` field: GitHub drops labels requested by issue
authors without triage permission. The intake workflow validates a complete
submission body, applies the `submission` label, and starts evaluation.

### Publishing exact solutions

LeanEval supports open science and does not prohibit publishing exact
solutions. Public solutions can help library development and let others
study and build on the work. They can also be copied directly or enter
future model-training data, reducing our ability to treat those problems
as unseen evaluation data.

The submission form asks you to choose one of three statuses:

- **Public**, with the actual publication date in `YYYY-MM-DD` format.
- **Private, but publication is planned**, with your current best estimate
  of the intended publication date in `YYYY-MM-DD` format. This is a
  submission-time snapshot, not a commitment.
- **Private, with no current publication plan**.

There is no required embargo. Please consider the tradeoffs when deciding
whether and when to publish. Methods, tooling, prompts, aggregate results,
and reusable library contributions can be published without publishing the
exact benchmark solutions.

### Audit archive

Every evaluated submission's compressed source tarball is retained
indefinitely, `age`-encrypted, in the private
[`leanprover/lean-eval-audit`](https://github.com/leanprover/lean-eval-audit)
repository so that the exact bytes evaluated for any past submission
remain recoverable if a comparator regression, soundness incident, or
research question requires re-examining them. Decryption keys are
held only by the small set of maintainers listed in
[`.audit/recipients.txt`](.audit/recipients.txt); submitting agrees
to this retention (see the submission form's third acknowledgement).

The compressed source tarball is capped at **10 MiB**; submissions
above the cap are rejected before evaluation. See
[`docs/audit-archive.md`](docs/audit-archive.md) for the design and
the decryption procedure.

## Results store

`results/` holds **machine-written** artifacts produced by the submission
CI. Do not edit them by hand.

```
results/
  <github-login>.json
```

One file per submitter; filenames use the lowercased GitHub login. Users
without a successful submission have no file.

Successes are **sticky**: once a `(user, declared model, problem, statement
revision)` tuple is
recorded it is never modified or removed, even if a later submission from
the same user no longer proves it.

### Record schema (v2)

```json
{
  "schema_version": 2,
  "user": "kim-em",
  "results": [
    {
      "result_id": "r2_...",
      "problem_id": "two_plus_two",
      "statement_revision": 1,
      "declared_model": "Claude Opus 4.7",
      "accepted_at": "2026-05-01T03:16:18Z",
      "benchmark_commit": "953d54a7af5038566775507761e48e365e7feb3b",
      "intake": {"kind": "issue", "issue_number": 45},
      "submission": {
        "kind": "gist",
        "repo": "kim-em/22bad2dccd67bcca0df87c01d072ef39",
        "ref": "567b8d1feebbc6ccbb1f8ebb0a7bbcf5e914f135",
        "public": true
      },
      "production_metadata": {}
    }
  ]
}
```

The exact identifier contract, full field definitions, v1 mapping, language-
neutral fixtures, and guarded migration procedure are documented in
[`docs/results-schema-v2.md`](docs/results-schema-v2.md). Readers accept v1
and v2 during migration; every newly changed file is written as v2.

### Write semantics

When the submission CI records a successful submission:

1. It reads and validates v1 or v2, or starts an empty v2 array.
2. It computes the stable ID from login, verbatim model, problem, and the
   statement revision frozen into the evaluation artifact.
3. If that ID exists, it does nothing; otherwise it appends a v2 record.
4. If at least one new record was added, the CI commits and pushes;
   otherwise it makes no commit.

Breaking schema changes bump `schema_version`; consumers should refuse a
file whose `schema_version` they do not know.

## How the pipeline fits together

```
submission issue on lean-eval-submissions
  → submission.yml: validate/label API intake if needed
  → checkout leanprover/lean-eval (problem set + probes), evaluate
  → write results/<login>.json here, push
  → repository_dispatch results-advanced → lean-eval-leaderboard redeploys
```

`submission-reconciler.yml` is an hourly safety net: it closes submission
issues that never received a bot comment (workflow disabled, runner died,
etc.).

## Operator notes

- Secrets, GitHub Apps, and branch protection: [`docs/ci-secrets.md`](docs/ci-secrets.md).
- Security model / threat analysis: [`SECURITY.md`](SECURITY.md).
- Local-only replay planner and disposable-VM contract:
  [`docs/replay-orchestrator.md`](docs/replay-orchestrator.md).
- Provider-neutral archive-key envelope and single-use capability claims:
  [`docs/key-capability-contract.md`](docs/key-capability-contract.md).
- `ci.yml` runs the Python test suite, `actionlint`, the workflow-pin
  audit, and `tests/test_submission_workflow.py` (a structural guard on
  `submission.yml`'s security-critical shape).
