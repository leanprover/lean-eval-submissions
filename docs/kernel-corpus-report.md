# Independent-kernel corpus report contract

This contract prepares and aggregates source-free evidence for an exact
candidate-checker series over an exact historical replay inventory. It is a
post-replay reporting boundary, not an executor: it does not fetch submission
source, run an exporter or checker, write Results or State, change the required
checker set, or approve promotion.

The tracked JSON fixtures are contract examples with synthetic digests. They
are not an approved checker configuration, replay inventory, execution record,
or promotion decision. The one-result `kernel-shadow-smoke.yml` remains a
non-authoritative compatibility preflight and cannot be substituted for this
full-corpus evidence.

## Artifact chain

Each layer is closed to unknown fields and validated both structurally and
semantically:

1. `kernel-checker-series-v1` binds the series name and exact candidate binary,
   exporter artifact, checker configuration, runner image, repositories,
   commits, protocols, architecture, operating system, and resource limits.
   `configuration_id` is the SHA-256 identity of the canonical complete object
   with only that ID omitted.
2. `kernel-corpus-inventory-v1` binds a migration cutoff, exact results-store
   commit and tree digest, the historical replay report digest, and every
   result/replay-task pair, positive replay attempt, and exact terminal verdict,
   terminal State event, and report-entry digests. Every runnable row also
   binds the digest of the source-free replay/export input. `inventory_id`
   binds the complete canonical object with only that ID omitted. Results and
   replay tasks are unique and sorted.
3. `kernel-corpus-shard-plan-v1` assigns every inventory result to exactly one
   shard by `sha256(result_id) mod shard_count`. Every attempt ID binds the
   configuration ID, inventory ID, result ID, replay task, positive replay
   attempt, terminal evidence tuple, and replay/export input. Recomputing the
   plan rejects changed configurations, inventories, attempts, shard counts,
   omissions, and duplicates.
4. The reviewed runner receives one regular raw input named
   `<attempt_id>.input` for each planned `run`; its raw SHA-256 must be the
   plan's `replay_export_input_sha256`. It emits one closed
   `kernel-corpus-runner-records-v1` bundle. The bundle binds the exact series,
   inventory, shard, ordered attempt and input identities, all measurements,
   resource disposition, transcript digest, and runner-attestation digest.
   The `source_free: true` field is an assertion made by that attested runner;
   the record-only adapter binds and preserves it but cannot independently
   prove that the opaque input or omitted transcript contained no source.
5. `kernel-corpus-observations-v1` must match one plan position for position.
   No submission source, source path, URL, repository, or ref belongs in this
   artifact. The evidence digest for an inherited unavailable result must be
   exactly the digest recorded by the inventory. Every action that executes the
   candidate/export lane embeds a content-addressed source-free receipt. The
   receipt binds its attempt and input, the exact series ID and digest, outcome,
   statistics, resource-limit disposition, transcript digest, and runner
   attestation digest. Pending or unavailable rows that did not execute cannot
   claim a receipt.
6. `kernel-corpus-report-v1` is a deterministic aggregate of every ordered
   shard. It binds the exact plan and observation sets, requires exactly-once
   full inventory coverage, records closed counters and performance summaries,
   and lists every terminal disagreement for adjudication.

JSON Schema cannot recompute content-derived identities, sorting, sharding, or
cross-artifact equality. The Draft 2020-12 schemas enforce the wire shape; the
Python validator enforces those semantic invariants. Consumers must run both.

## Outcome boundaries

The five checker terminal outcomes are distinct:

- `accepted`
- `rejected`
- `declined`
- `crashed`
- `timed_out`

They are never used for an input that historical replay has not made ready.
Source, replay, and export unavailability remain separately visible as
`source_unavailable`, `replay_unavailable`, and `export_unavailable` with an
evidence digest. `replay_pending` remains pending without invented evidence or
a terminal verdict. An exporter that cannot represent the input records
`export_format_unsupported` as pending review with evidence; it is not a
checker rejection or a generic crash.

Only completed terminal observations contribute to performance statistics.
The report uses deterministic upper-median and nearest-rank p95 wall time,
minimum/maximum/sum wall time, maximum peak memory, and total checker
invocations. No statistic is inferred for pending or unavailable results.
Each executed measurement is bounded by the series wall-time and memory limits;
checker invocation counts and aggregate sums are bounded to interoperable safe
integers. Export-unavailable and export-format outcomes require exactly zero
checker invocations; every checker terminal outcome requires at least one.
`timed_out` must carry the wall-time disposition, while a memory-limit
disposition is valid only for a crash at the configured bound.

Any terminal candidate outcome that differs from the historical authoritative
outcome creates an `adjudication: required` record and a blocking reason. The
report's `promotion.automated_eligibility` is always `false`; automation may
add blocking reasons but can never grant the human promotion approval.

## Offline preparation and aggregation

Validate the exact inputs and prepare an empty output directory:

```bash
python scripts/kernel_corpus_report.py validate-series --input series.json
python scripts/kernel_corpus_report.py validate-inventory --input inventory.json
python scripts/kernel_corpus_report.py prepare-shards \
  --series series.json \
  --inventory inventory.json \
  --shard-count 16 \
  --output-dir plans
```

After a separately reviewed runner has produced the exact raw input files and
one runner-record bundle for a shard, materialize the observation file:

```bash
python scripts/kernel_corpus_runner_adapter.py \
  --series series.json \
  --inventory inventory.json \
  --plan plans/shard-0000.json \
  --inputs-dir inputs/shard-0000 \
  --records runner-records/shard-0000.json \
  --output observations/shard-0000.json
```

Input-directory membership must be exact: inherited unavailable or pending
attempts have no input file, while each `run` attempt has exactly its one
content-addressed file. This is an operator-side precondition proving that the
reviewed bytes are present at materialization time; it is not independent proof
that the separately attested runner consumed those bytes, and it is not a new
field in the observation receipt. The runner-record list must contain exactly
the `run` attempts in plan order. Missing, extra, reordered, mixed-pin,
over-limit, or contradictory records fail closed. The adapter synthesizes inherited
`source_unavailable`, `replay_unavailable`, and `replay_pending` observations
from the inventory without adding statistics or receipts. It maps a reviewed
runner record only to one of the five checker outcomes, `export_unavailable`,
or `export_format_unsupported`, then validates the completed observation shard
against the existing semantic and JSON Schema contracts.

The adapter does not execute any process and has no network, credential,
repository-write, Results, State, or promotion interface. The raw-input and
record JSON reads are regular-file, no-follow, count-, byte-, depth-, and
node-bounded. Observation publication is exclusive, no-follow, atomic, and
never overwrites an existing path. The input directory itself must exist and
must not be a symlink, including for a shard with no planned `run` attempts.

After every separately reviewed shard has been materialized, aggregate them in
deterministic filename order:

```bash
python scripts/kernel_corpus_report.py aggregate \
  --series series.json \
  --inventory inventory.json \
  --plans-dir plans \
  --observations-dir observations \
  --output report.json
```

The tool refuses a nonempty preparation directory and refuses incomplete,
mixed, reordered, duplicated, or altered plan/observation sets. JSON reads are
per-file and aggregate-directory byte-, node-, depth-, and file-count-bounded;
reject duplicate object keys; and
accept only regular non-symlink files. Shard directories reject unknown names,
FIFOs, devices, links, and membership differences. Outputs use no-follow,
exclusive, same-directory atomic publication and never overwrite an existing
path. Every runtime input and generated artifact is checked against its Draft
2020-12 schema as well as the cross-artifact semantic validator. The tool
performs no network access and has no credential or repository-write interface.

## Current rollout status

This repository now provides the preparatory contract, a fail-closed offline
runner-record adapter, deterministic aggregator, schemas, fixtures, and hostile
tests. It does not provide or claim a corpus execution. An actual corpus report
is still blocked on completion and review of the historical replay inventory;
production of each ready row's real source-free replay/export input artifact;
and a separately reviewed exact-image runner that executes the pinned exporter,
checker, and candidate and produces the attested record bundle. Those missing
artifacts are the execution dependency: synthetic fixtures cannot satisfy it.
AWS-backed private archive execution is not provisioned by this change.
Production intake and automatic publication remain outside this contract.
