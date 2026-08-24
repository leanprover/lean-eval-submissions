# Historical replay inventory

The lifecycle overhaul requires every accepted result at the migration cutoff
to be replayed or explicitly marked permanently unavailable with reviewed
evidence. The isolated public and private staging proofs do not satisfy that
corpus gate.

`scripts/inventory_historical_replay.py` creates the deterministic first input
to that work. It validates the complete schema-version-2 results store,
recomputes every stable result identity through the shared validator, rejects
duplicates, binds an exact source commit and canonical store digest, and sorts
all entries by `result_id`. It also fails closed before parsing or rendering if
the results directory, an individual file, the aggregate store, a per-file or
aggregate record count, or the canonical inventory exceeds its explicit bound.

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

The protected `Build historical replay inventory` workflow is the contract-only
generation path. Dispatch it from the exact `lean-eval-dispatch/<full-commit>` tag and
provide that same commit, the independently reviewed canonical store digest,
the exact accepted-result count, and the explicit contract-only confirmation.
It resolves lightweight and annotated tags without accepting ambiguous remote
output, checks that the selected commit remains an ancestor of API-verified
protected `main`, checks out that commit without credentials, and requires a
clean `results/` tree. It then generates the inventory twice, checks byte
identity, validates the JSON against the checked-in Draft 2020-12 schema with a
pinned validator, checks every count, digest, ordering, classification, size,
and private-source-minimization invariant, and uploads only the source-free JSON
artifact.

The uploaded artifact and workflow summary are transient transport, not durable
qualification evidence. Before any corpus replay gate can cite an inventory, a
follow-up protected-main PR must commit the reviewed canonical inventory (or a
content-addressed immutable equivalent) and an evidence record binding the
workflow repository, run ID and attempt, selected source commit, canonical
store digest, result count, and inventory SHA-256. Review must verify the run
conclusion and exact artifact bytes. The inventory workflow deliberately has no
write credential and cannot satisfy this durable-evidence gate by itself. A
local invocation is useful for reviewing expected inputs but is not publication
or qualification evidence.
