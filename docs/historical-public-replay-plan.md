# Historical public replay seed plan

The reviewed GitHub-evidence aggregate identifies which legacy public results
have an exact surviving source and which historical workflow accepted them. It
is not itself an authoritative replay request. This stage converts only its
`resolved` submission groups into a deterministic, source-free seed plan.

The bridge deliberately stops before State enqueue. Historical issue results
are immutable schema-version-2 records, but they do not have the modern
`submission.received` → `result.recorded` lifecycle. The current State
materializer builds replay tasks only by joining a `replay.enqueued` event to
that modern lifecycle, including a UUID submission ID and archive receipt.
Inventing either for a legacy public result would create false authority.
Legacy `result.claimed` events anchor ownership and the Results-store record,
but the current replay materializer does not admit them as replay authority.

Every plan is therefore locked to:

```json
{
  "activation_status": "blocked",
  "activation_requirement": "legacy_public_result_replay_authority_v1"
}
```

Removing that blocker requires the separately reviewed, system-owned
`historical_result.replay_authorized` State contract. Production State has no
historical `result.claimed` anchors, so making an owner claim a prerequisite
would block corpus completion on every historical owner. One authorization
event instead binds one exact seed-plan request/result/evidence tuple,
recomputes the stable result ID from canonical lowercase `owner_login`,
verbatim model, problem, and
statement revision, and carries only the public source, benchmark/toolchain,
and immutable Results snapshot bindings. It creates no acceptance,
publication, credit, or owner-metadata authority. An ordinary
`replay.enqueued` event may causally follow it and retain the existing replay
task ID and verdict lifecycle.

The authorization contract must not mint a fake submission lifecycle, require
an owner action, expose a private record, or attach an encrypted-archive
locator to a public source.

## Deterministic inputs

`scripts/build_public_replay_toolchain_registry.py` validates the complete
evidence aggregate and exact resolution-request bytes, selects only resolved
benchmark commits, and reads `lean-toolchain` from those immutable public Git
objects. Its output retains only the benchmark commit, exact release name, and
SHA-256 of the original `lean-toolchain` blob. The full benchmark checkout is
not uploaded.

`scripts/prepare_public_replay_plan.py` then requires all of the following:

- the byte-canonical inventory and requests whose digests are named by the
  aggregate;
- the byte-canonical reviewed workflow-definition registry;
- the complete aggregate, revalidated down to every reconstructed shard;
- an exact results checkout at the aggregate's source commit; and
- a toolchain registry with exactly one entry for every and only every
  resolved benchmark commit.

It recomputes the inventory and request set from the Results checkout. For
each resolved group it binds the public source commit, benchmark commit and
toolchain blob, historical evaluator commit/workflow digest/run identity,
issue identity, accepted result IDs, and an exact Results snapshot file/tree
digest. Historical owner casing is case-folded to the canonical lowercase
`owner_login`, checked against every grouped result, and used to recompute each
stable result ID. `historical_accepted_at` preserves the legacy Results
second-precision timestamp exactly; it is not silently rewritten as a normal
millisecond-precision State event time. Non-resolved requests never enter the
plan. The entire aggregate digest
remains a top-level dependency, so the compact projection cannot be detached
from omitted candidate evidence or pending classifications.

Authorization is also separate from execution-profile readiness. The seed
plan remains `execution_profile_status: unresolved` under
`historical_benchmark_toolchain_execution_profile_v1`; authorizing a result
does not claim that the current v4.33 runner can execute its historical
toolchain. In the reviewed local corpus, 101 of 135 results are not v4.33 and
91 use prerelease toolchains. Each exact historical toolchain needs a reviewed
compatible execution profile before its ordinary enqueue event. The
source-free per-benchmark build matrix and the remaining qualification boundary
are specified in `docs/historical-public-replay-profiles.md`.

The machine-readable contracts are:

- `schemas/historical-public-replay-toolchains-v1.schema.json`; and
- `schemas/historical-public-replay-plan-v1.schema.json`.

The plan is a replay seed, not `replay-execution-request-v1`. It contains no
submission source bytes, issue bodies, workflow logs, credentials, State
writer, archive locator, synthetic submission ID, replay task ID, attempt, or
verdict.
Consumers must run the producer validator and verify the exact plan digest;
JSON Schema validation alone does not prove request/result identity uniqueness
or cross-field equality.

## Protected publication workflow

Once a reviewed source-free aggregate is committed at the constrained
`evidence/historical-public-replay-github-evidence-<source-commit-prefix>.json` path,
dispatch `historical-public-replay-plan.yml` from the
exact `lean-eval-dispatch/<full-commit>` tag. Supply the aggregate's reviewed
SHA-256 and exact resolved/pending counts. The workflow proves the tag and
protected-main ancestry, recomputes the inventory and resolution requests from
the aggregate's historical Results commit, resolves toolchains from public
`leanprover/lean-eval` Git objects, builds the plan twice, compares the bytes,
and uploads only the blocked plan and toolchain registry.

The workflow has explicit contents-read plus GitHub's implicit metadata-read
permission and no write permission. Its default token is used to fetch the
public benchmark checkout but is not persisted. It does not enable intake or
replay, append State, fetch submission source, assume AWS authority, or invoke
the replay controller. Publishing a plan therefore does not satisfy the corpus
execution gate.

The final source-free plan covers all 128 replayable request groups and 194
accepted results. It is stored at
`evidence/public-replay/plans/d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e.json`;
its 35-commit, five-toolchain registry is retained at
`evidence/public-replay/toolchains/4f2f3737d79e6abd6c169ebdde3f2218157d8f6c482a85ad2026821a4b8e81a0.json`.
These files are replay inputs; they do not qualify execution profiles, enqueue
State work, or decide that unavailable sources are permanently unrecoverable.
The later reviewed State ledger has since recorded terminal public-source
unavailability for 20 of these Results across eight complete requests. The
source plan remains byte-for-byte evidence of its earlier classification, but
the retained batch finalizer now derives the authoritative 174-task subset from
the validated current State contract. It cannot enqueue any subject with an
existing terminal disposition.
