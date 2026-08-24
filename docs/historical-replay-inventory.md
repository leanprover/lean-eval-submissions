# Historical replay inventory

The lifecycle overhaul requires every accepted result at the migration cutoff
to be replayed or explicitly marked permanently unavailable with reviewed
evidence. The isolated public and private staging proofs do not satisfy that
corpus gate.

`scripts/inventory_historical_replay.py` creates the deterministic first input
to that work. It validates the complete schema-version-2 results store,
recomputes every stable result identity through the shared validator, rejects
duplicates, binds an exact source commit and canonical store digest, and sorts
all entries by `result_id`.

The public inventory deliberately does not copy a private repository or commit
into each private entry. It classifies records only as:

- `public_source_probe_pending`, retaining the already-public exact source
  locator for a later anonymous availability probe; or
- `private_archive_migration_pending`, requiring the separately protected
  historical envelope migration and exact archive-to-result binding.

These are pending classifications, not terminal unavailability reasons and not
authoritative replay queue events. Temporary missing credentials, provider
capacity, migration work, or policy review must never be converted into
`replay.unavailable`. A later reviewed corpus workflow must probe each public
ref, correlate each private result with its migrated archive, resolve exact
historical toolchains, and generate State enqueue or permanent-unavailability
events with exact evidence.

Example:

```bash
python scripts/inventory_historical_replay.py \
  --results-root results \
  --source-commit "$(git rev-parse HEAD)" \
  --output /tmp/historical-replay-inventory.json
```

The output path must not already exist. The schema is
`schemas/historical-replay-inventory-v1.schema.json`.

The protected `Build historical replay inventory` workflow is the publication
path. Dispatch it from the exact `lean-eval-dispatch/<full-commit>` tag and
provide that same commit, the independently reviewed canonical store digest,
and the exact accepted-result count. It verifies the remote immutable tag,
checks out that commit without credentials, requires a clean `results/` tree,
generates the inventory twice, checks byte identity and all count, digest,
ordering, classification, and private-source-minimization invariants, then
uploads only the source-free JSON artifact. A local invocation is useful for
reviewing the expected inputs but is not publication evidence by itself.
