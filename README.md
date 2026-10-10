# lean-eval-submissions

The submission pipeline and the stored results for the
[lean-eval](https://github.com/leanprover/lean-eval) benchmark.

This repository owns two things:

- **The submission process** — the server-dispatched `submission` workflow
  that archives exact source, evaluates it with
  [comparator](https://github.com/leanprover/comparator), records lifecycle
  state, and stores the outcome.
- **The results store** — `results/<github-login>.json`, the append-only
  public log of solved problems.

The benchmark problem set, the `lean-eval` CLI, and the comparator/landrun
security model live in [`leanprover/lean-eval`](https://github.com/leanprover/lean-eval).
The public leaderboard that renders these results is
[`leanprover/lean-eval-leaderboard`](https://github.com/leanprover/lean-eval-leaderboard)
(**[view it →](https://lean-lang.org/eval/)**).

## Submitting a solution

Submit through the
[**LeanEval submission service**](https://lean-lang.org/eval/submit/).
GitHub Issues are no longer a submission path; existing issues and historical
results remain unchanged.

Before submitting through the service, install both read-only Apps on only the
repository you intend to submit:

- [LeanEval Source Reader](https://github.com/apps/lean-eval-source-reader/installations/new)
  verifies the repository and exact source commit during intake.
- [lean-eval-bot](https://github.com/apps/lean-eval-bot/installations/new)
  lets the archive and evaluation workflow fetch that same commit.

The service verifies both installations and exact-commit access before it
records or dispatches a submission. This is enforced for browser and headless
submission clients; it is not a form checkbox or user assertion.

The service accepts a problem ID, declared model, exact repository and commit,
publication choice, production metadata, and acceptance of the current terms.
It resolves the problem group and current statement revision itself from the
protected LeanEval catalog. Formalization problems must be visible and active;
software-verification problems may be visible and draft or active. Clients do
not supply either canonical field.

Scheduled publication is the recommended default. A public repository must use
scheduled publication. A private repository may instead keep accepted source
private and irreversibly schedule it later. Accepted scheduled source is
released under Apache License 2.0 exactly two UTC calendar months after
acceptance.

Submitting the same owner, repository, commit, problem, and resolved statement
revision again returns the original submission instead of dispatching duplicate
work. An owner may have at most four active submissions; completed and terminal
submissions, and incomplete submissions older than 24 hours, do not occupy a
slot.

The submission service evaluates only the exact problem selected in the request.
The exact repository snapshot is privately fetched, archived, built, and
executed. It is not published before the selected release time; only results
and submitted metadata are public during the embargo or when source is
withheld. Do not include secrets in a submitted repository or its metadata.

If your submission lives in a **private** repository, install the
`lean-eval-bot` GitHub App on it so the CI can clone it:
**<https://github.com/apps/lean-eval-bot>**.

### Headless service contract

Headless clients first request a source-bound challenge from
`POST /api/v1/agent/challenges`, prove control through the returned exact tag
and secret Gist, and then send that signed challenge to
`POST /api/v1/agent/submissions`. The submission object has this closed v2
shape:

```json
{
  "schema_version": 2,
  "problem_id": "two_plus_two",
  "declared_model": "Example Model",
  "source_repository": "owner/repository",
  "source_commit": "0123456789abcdef0123456789abcdef01234567",
  "publication_choice": "scheduled",
  "production_metadata": {},
  "terms_version": "lean-eval-intake-terms-v1",
  "terms_accepted": true
}
```

Unknown fields and older schema versions are rejected. The server returns the
canonical problem group and statement revision in the receipt.

### Publishing exact solutions

LeanEval supports open science and does not prohibit publishing exact
solutions. Public solutions can help library development and let others
study and build on the work. They can also be copied directly or enter
future model-training data, reducing our ability to treat those problems
as unseen evaluation data.

The service asks whether accepted source should be scheduled for automatic
release under Apache-2.0 two UTC calendar months after acceptance or withheld.
Scheduled release is the recommended choice. A submitter who initially chooses
`withheld` may later make the one-way change to `scheduled`; a scheduled release
cannot be changed back to withheld. Methods, tooling, prompts, aggregate
results, and reusable library contributions can be published without
publishing the exact benchmark solutions.

### Audit archive

Every service submission's compressed source tarball is retained indefinitely,
including submissions that reject or fail. Each archive uses a fresh data key
and a schema-version-3 per-submission key envelope. The encrypted archives live in the private
[`leanprover/lean-eval-audit`](https://github.com/leanprover/lean-eval-audit)
repository so that the exact bytes evaluated for any past submission
remain recoverable if a comparator regression, soundness incident, or
research question requires re-examining them. Archive-key wrapping and release
unwrapping use separate, least-privilege AWS roles. Submitting explicitly agrees
to this retention and to private fetch, archive, build, and execution before
State accepts the request.

The compressed source tarball is capped at **100 MB**; submissions
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

### Results record schema version 2

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
      "intake": {
        "kind": "server",
        "submission_id": "01a0603c-6189-7751-9c43-c904b50b477a"
      },
      "submission": {
        "kind": "github_repo",
        "repo": "kim-em/lean-eval-solution",
        "ref": "567b8d1feebbc6ccbb1f8ebb0a7bbcf5e914f135",
        "public": true
      },
      "production_metadata": {}
    }
  ]
}
```

The exact identifier contract, full field definitions, schema version 1
mapping, language-neutral fixtures, and guarded migration procedure are documented in
[`docs/results-schema-v2.md`](docs/results-schema-v2.md). Readers accept
results schema versions 1 and 2 during migration; every newly changed file is
written using schema version 2.

### Write semantics

When the submission CI records a successful submission:

1. It reads and validates schema version 1 or 2, or starts an empty
   schema version 2 array.
2. It computes the stable ID from login, verbatim model, problem, and the
   statement revision frozen into the evaluation artifact.
3. If that ID exists, it does nothing; otherwise it appends a
   schema version 2 record.
4. If at least one new record was added, the CI commits and pushes;
   otherwise it makes no commit.

Breaking schema changes bump `schema_version`; consumers should refuse a
file whose `schema_version` they do not know.

## How the pipeline fits together

```
submission service
  → verify the exact source and current catalog problem
  → atomically accept append-only State and enqueue dispatch
  → archive the immutable source snapshot with its per-submission envelope
  → evaluate only the accepted problem and record its terminal lifecycle
  → write any accepted immutable Result
  → refresh the lifecycle-aware leaderboard
```

## Operator notes

- Secrets, GitHub Apps, and branch protection: [`docs/ci-secrets.md`](docs/ci-secrets.md).
- Security model / threat analysis: [`SECURITY.md`](SECURITY.md).
- Local-only replay planner and disposable-VM contract:
  [`docs/replay-orchestrator.md`](docs/replay-orchestrator.md).
- Source-minimized historical public replay evidence and its read-only token
  boundary: [`docs/public-replay-resolution.md`](docs/public-replay-resolution.md).
- Provider-neutral archive-key envelope and single-use capability claims:
  [`docs/key-capability-contract.md`](docs/key-capability-contract.md).
- Trusted provider-neutral archive writer and server-archive workflow:
  [`scripts/archive_envelope.py`](scripts/archive_envelope.py) and
  [`.github/workflows/server-archive.yml`](.github/workflows/server-archive.yml).
- AWS KMS/DynamoDB adapter and linted SAM infrastructure; production archive
  Wrap is connected, while production replay remains packet-gated under the
  standing authorization: [`scripts/aws_key_adapter.py`](scripts/aws_key_adapter.py) and
  [`infrastructure/aws-key-adapter/template.yaml`](infrastructure/aws-key-adapter/template.yaml).
- Concise operator walkthrough for the dedicated account:
  [`docs/aws-key-adapter-setup.md`](docs/aws-key-adapter-setup.md).
- `ci.yml` runs the Python test suite, `actionlint`, the workflow-pin
  audit, and `tests/test_submission_workflow.py` (a structural guard on
  `submission.yml`'s security-critical shape).
