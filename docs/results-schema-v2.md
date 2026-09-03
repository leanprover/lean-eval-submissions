# Results schema version 2

Results schema version 2 is a flat, append-only base-results format. During
migration, readers accept schema versions 1 and 2; writers always emit schema
version 2. Later corrections, model aliases, retractions, and publication
actions belong in the state event log and do not mutate these records.

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

## Exact-commit recording receipt

Server intake does not infer State advancement from a successful record job.
After the recorder has pushed, `scripts/build_result_receipt.py` validates the
canonical schema-version-2 file and emits a source-free receipt naming the
result ID, repository, environment branch, exact commit, path, and tree
digest. Staging writes `staging-results`; production writes `main`. Historical
legacy issue-intake records remain in `main`. Only production writes trigger
the public leaderboard.

For the exact per-user file bytes at `path`:

```text
entry = RFC8785_CANONICAL_JSON([{
  "path": path,
  "sha256": lowercase_hex(SHA256(file_bytes)),
  "size": len(file_bytes)
}])

result_tree_digest = lowercase_hex(
  SHA256(UTF8("lean-eval-result-tree-v1\0") + UTF8(entry))
)
```

The source-free callback job has no recorder credential. The submission
Worker independently fetches the named blob through an allowlist restricted to
`leanprover/lean-eval-submissions/results/<login>.json` at the exact commit,
recomputes both IDs, and atomically appends `result.recorded`, the optional
`release.scheduled`, and the updated submission view. Scheduled releases use
the evaluation-acceptance timestamp plus two calendar months, clamped to the
last day of the target month. Withheld and open-conjecture results are not
scheduled.

If the deterministic result ID is already guarded by an earlier `claimed` or
`recorded` authority, the first authority remains canonical. Instead of
retrying forever or adopting that authority, the callback atomically appends
one `submission.result_identity_conflicted` event and a schema-version-3
submission view whose `result_disposition.status` is `identity_conflict`.
The view keeps its flat `result_id` and `result_event_id` null. This terminal
receipt creates no second result authority, reservation, amendment, owner
association, or release schedule; an exact callback retry is read-only.
Submission metadata amendments preserve the terminal disposition. Publication
opt-in rejects the conflicted view rather than presenting it as a result that
is still pending.

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

Legacy schema version 1 results are assigned `statement_revision = 1`. The migration maps
`solved_at` to `accepted_at`, nests the source fields under `submission`, nests
`issue_number` under `intake`, and moves the three optional production and
publication fields into `production_metadata`. No legacy field is discarded.

## Migration tool

`scripts/migrate_results_v2.py` defaults to a read-only dry run. It reports
the captured commit, source and output record counts, source digest, canonical
output digest, changed files, duplicate IDs, and an exact schema version 1
projection check.
`--apply` additionally requires all three reviewed expectations:
`--expect-source-digest`, `--expect-record-count`, and
`--expect-output-digest`. This makes applying a report to different live data
fail closed.

Run the workflow once with `apply=false` and review its report artifact. A
later `apply=true` dispatch requires the report's exact source commit, source
digest, record count, and output digest as workflow inputs. The apply run
recomputes the report after acquiring the writer lock and refuses any mismatch;
it never treats a report generated inside the apply run as human approval.

The apply workflow and record jobs share the logical writer group
`results-store-writer`. GitHub Actions concurrency is used to serialize
migration runs, but not record jobs: Actions keeps only one pending run per
group and would cancel older submissions during a burst. Instead, apply first
commits `.results-store-writer-lock.json`; record jobs repeatedly fetch
`origin/main` and wait while that lock exists. CAS pushes close the race between
checking and acquiring the lock. Migration removes the lock in the same commit
that writes schema version 2. If that final push fails, the lock deliberately remains for
operator review rather than allowing mixed writes. After confirming the lock's
recorded workflow URL and repository state, an operator can rerun apply with
`resume_locked_migration`; the workflow validates the lock group and creates a
new fresh report before attempting the migration again.
