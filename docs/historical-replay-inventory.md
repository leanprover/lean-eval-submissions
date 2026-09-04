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

The private correlation contract is implemented by
[`classify_historical_private_archives.py`](../scripts/classify_historical_private_archives.py)
and documented in
[`historical-private-archive-crosswalk.md`](historical-private-archive-crosswalk.md).
It performs the private locator join only in memory and emits a closed,
source-free classification for every private result. An `archive_not_found`
entry remains pending and cannot be laundered into permanent unavailability.

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

The first reviewed cutoff inventory is stored at
`evidence/historical-replay/inventories/bb405fbabe084e106ad5500b455a05ba1e1d54175d1964db3aebcc3b6ea3fce3.json`.
Its 633 public entries remain
`public_source_probe_pending`, and its 668 private entries remain
`private_archive_migration_pending`. It proves neither source availability nor
archive migration, performs no replay, qualifies no result or corpus, and
authorizes no publication.

Issue intake remains open during the server-intake overlap, so the first
reviewed inventory is a baseline rather than a promise that the Results store
will stay at 1,301 entries. `reconcile_historical_replay_inventory_delta.py`
compares a later full inventory with that exact baseline. It rejects deletion
or mutation of any baseline entry, partitions every new entry by its exact
canonical intake identity, and emits the issue-intake entries as the historical
delta. Its separate source-free server exclusion set binds every post-baseline
server Result to its submission, Result file, and Result-tree digest. Both
source commits, Results-store digests, full-inventory digests, and counts remain
bound. The output contract is
`schemas/historical-replay-inventory-delta-v1.schema.json`.

Set `confirm_append_only_delta` on the protected inventory workflow to build
the full current inventory twice and independently build the baseline delta
twice. The additional artifact is still contract-only transient evidence: it
does not probe public sources, migrate a private archive, enqueue replay, write
State or Results, enable intake, or close issue intake. At the final announced
issue-intake cutoff, the Results-only protected-main commit enters the
reviewer-gated, deployment-free immutable-tag promotion workflow. Approve that
exact commit only after its protected-main CI succeeds, then dispatch the
inventory workflow from the resulting `lean-eval-dispatch/<full-commit>` tag.
Commit and review both the full inventory and its delta, then feed every
issue-intake delta entry into the same public/private classification and
terminal replay gates as the baseline corpus. Before activation, require every
excluded server Result to have one matching `result.recorded` event in exact
validated State. Retain the legacy issue-intake decryption authority until that
final delta has closed.

The observed counts before cutoff do not freeze the final delta. Later issue
acceptances must be included by rerunning the workflow at the actual cutoff;
later server-native Results remain outside the historical replay corpus.
