# Results schema v2

Schema v2 is a flat, append-only base-results format. During migration,
readers accept v1 and v2; writers always emit v2. Later corrections, model
aliases, retractions, and publication actions belong in the state event log
and do not mutate these records.

The machine-readable envelope is `schemas/results-v2.schema.json`. The Python
validator additionally recomputes every stable ID and enforces uniqueness and
cross-field invariants that JSON Schema cannot express.

## Stable `result_id` contract

For a GitHub login `user`, the exact submitted model label `declared_model`,
problem identifier `problem_id`, and positive integer `statement_revision`:

```text
identity = RFC8785_CANONICAL_JSON([
  lowercase(user),
  declared_model,
  problem_id,
  statement_revision
])

result_id = "r2_" + lowercase_hex(
  SHA256(UTF8("lean-eval-result-v2\0") + UTF8(identity))
)
```

The model string is verbatim: do not trim, normalize Unicode, or apply a
model alias. Intake IDs, source refs, timestamps, aliases, and amendments are
not inputs. Retries of the same tuple are sticky no-ops; a new statement
revision is a new result. Model labels later merged into one canonical model
retain their distinct base records. The language-neutral conformance vectors
are in `tests/fixtures/result_id_vectors.json`.

The identifier input is an RFC 8785 array containing only strings and a
positive integer. Implementations must reject lone Unicode surrogates. GitHub
login case is deliberately folded; all other strings are preserved exactly.

## Per-user file

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

`intake.kind` is `issue` with a positive `issue_number`, or `server` with a
canonical lowercase UUIDv7 `submission_id`. `submission.kind` is `github_repo`
or `gist`. The structured
`production_metadata` object preserves submission-time declarations; changes
after acceptance are events.

Legacy v1 results are assigned `statement_revision = 1`. The migration maps
`solved_at` to `accepted_at`, nests the source fields under `submission`, nests
`issue_number` under `intake`, and moves the three optional production and
publication fields into `production_metadata`. No legacy field is discarded.

## Migration tool

`scripts/migrate_results_v2.py` defaults to a read-only dry run. It reports
the captured commit, source and output record counts, source digest, canonical
output digest, changed files, duplicate IDs, and an exact v1 projection check.
`--apply` additionally requires all three reviewed expectations:
`--expect-source-digest`, `--expect-record-count`, and
`--expect-output-digest`. This makes applying a report to different live data
fail closed.

The apply workflow and record jobs share the logical writer group
`results-store-writer`. GitHub Actions concurrency is used to serialize
migration runs, but not record jobs: Actions keeps only one pending run per
group and would cancel older submissions during a burst. Instead, apply first
commits `.results-store-writer-lock.json`; record jobs repeatedly fetch
`origin/main` and wait while that lock exists. CAS pushes close the race between
checking and acquiring the lock. Migration removes the lock in the same commit
that writes v2. If that final push fails, the lock deliberately remains for
operator review rather than allowing mixed writes. After confirming the lock's
recorded workflow URL and repository state, an operator can rerun apply with
`resume_locked_migration`; the workflow validates the lock group and creates a
new fresh report before attempting the migration again.
