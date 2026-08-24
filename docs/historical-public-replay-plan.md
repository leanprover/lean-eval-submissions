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

Removing that blocker requires a separately reviewed State/replay contract.
That contract must either make an exact `result.claimed` record eligible for a
public-only replay task or define a system-owned historical import event. It
must not mint a fake submission lifecycle, require an owner to alter the base
result, or attach an encrypted-archive locator to a public source.

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
digest. Non-resolved requests never enter the plan. The entire aggregate digest
remains a top-level dependency, so the compact projection cannot be detached
from omitted candidate evidence or pending classifications.

The machine-readable contracts are:

- `schemas/historical-public-replay-toolchains-v1.schema.json`; and
- `schemas/historical-public-replay-plan-v1.schema.json`.

The plan is a replay seed, not `replay-execution-request-v1`. It contains no
submission source bytes, issue bodies, workflow logs, credentials, State
writer, archive locator, synthetic submission ID, replay task ID, attempt, or
verdict.

## Protected publication workflow

Once a reviewed source-free aggregate is committed below
`evidence/public-replay/`, dispatch `historical-public-replay-plan.yml` from the
exact `lean-eval-dispatch/<full-commit>` tag. Supply the aggregate's reviewed
SHA-256 and exact resolved/pending counts. The workflow proves the tag and
protected-main ancestry, recomputes the inventory and resolution requests from
the aggregate's historical Results commit, resolves toolchains from public
`leanprover/lean-eval` Git objects, builds the plan twice, compares the bytes,
and uploads only the blocked plan and toolchain registry.

The workflow has contents-read permission only. It does not enable intake or
replay, append State, fetch submission source, assume AWS authority, or invoke
the replay controller. Publishing a plan therefore does not satisfy the corpus
execution gate.

At the reviewed aggregate generated from submissions commit `5746f90`, the
local bridge check produced 69 resolved submission groups, 135 accepted
results, 25 benchmark toolchains, and 246 still-pending groups. These counts
are descriptive local verification until the aggregate and protected plan run
are reviewed and published.
