# Historical public replay unavailability review

The final public GitHub-evidence aggregate classifies 187 requests / 439
accepted results as `source_unavailable`. That observation is not a permanent
replay verdict. A repository or Gist can reappear, a provider can recover, and
a probe can be wrong. Permanent unavailability therefore requires a separate,
exact-byte review.

`scripts/prepare_public_replay_unavailability.py` creates and verifies that
review boundary. It never reads solution source and has no State, Results,
Cloudflare, AWS, release, or publication credential.

## Candidate preparation

Preparation starts from the exact inventory, exact resolution requests, frozen
Results tree, final adjudicated aggregate, workflow registry, and legacy
adjudication registry. It reconstructs the inventory and requests from Results,
validates the aggregate with the existing full runtime validator, and refuses
any indeterminate, ambiguous, unreviewed, or missing-evidence classification.

Every candidate binds:

- its request and all accepted result IDs;
- the accepted public repository/Gist and immutable commit;
- the benchmark, declared model, acceptance time, and issue identity;
- the selected historical workflow/run identity;
- both issue-repository candidate statuses and the digest of the exact full
  aggregate resolution;
- each Results path, file digest, tree digest, and source commit; and
- a digest of the individual candidate before review.

The output is a content-addressed manifest plus compact, content-addressed
shards. The manifest binds every shard's digest, byte count, index, request
range, and request/result counts. Shards contain at most 32 requests and are
canonical compact JSON. The proposed reason remains
`source_ref_permanently_unavailable`, but every entry is `pending`; no State
append, replay, permanent-unavailability, or corpus-completion claim is made.

```console
python scripts/prepare_public_replay_unavailability.py prepare \
  --inventory /frozen/inventory.json \
  --resolution-requests /frozen/resolution-requests.json \
  --results-root /frozen/results \
  --aggregate evidence/historical-public-replay-github-evidence-ba5f578.json \
  --workflow-registry configuration/public-replay-workflow-definitions-v1.json \
  --legacy-adjudication-registry configuration/public-replay-legacy-adjudications-v1.json \
  --output-directory /new/unavailability-candidate-bundle
```

The manifest schema is
`schemas/public-replay-unavailability-candidates-v1.schema.json`; the shard
schema is
`schemas/public-replay-unavailability-candidate-shard-v1.schema.json`.

## Retained pending evidence

The deterministic preparation over aggregate `ba816b52…` reconstructs exact
inventory digest `1a747133…` and resolution-request digest `bf78ab88…` from the
byte-identical 1,301-result snapshot. The retained pending bundle is:

- manifest
  `evidence/public-replay/unavailability-candidate-bundle-v1/0177bec519a803e52652368572ec06b5bcdd3fdc3591c06e2e25b14cf5ff725e.json`;
- six shards beneath the adjacent `shards/` directory;
- 187 requests / 439 results; and
- 522,968 total bytes, with the largest file 140,398 bytes.

Every filename is its exact file SHA-256. The compact shards avoid GitHub's
large-diff blind spot while retaining the enriched identity needed for review.
Detailed GitHub observations are not duplicated: each candidate binds its exact
aggregate resolution digest, and the manifest binds the exact aggregate bytes.

## Mechanical verification and review

Before writing a review registry, verify the retained bytes against all frozen
inputs:

```console
python scripts/prepare_public_replay_unavailability.py verify \
  --inventory /frozen/inventory.json \
  --resolution-requests /frozen/resolution-requests.json \
  --results-root /frozen/results \
  --aggregate evidence/historical-public-replay-github-evidence-ba5f578.json \
  --workflow-registry configuration/public-replay-workflow-definitions-v1.json \
  --legacy-adjudication-registry configuration/public-replay-legacy-adjudications-v1.json \
  --candidate-manifest evidence/public-replay/unavailability-candidate-bundle-v1/0177bec519a803e52652368572ec06b5bcdd3fdc3591c06e2e25b14cf5ff725e.json \
  --candidate-shards evidence/public-replay/unavailability-candidate-bundle-v1/shards
```

Verification rederives every candidate and every manifest/shard byte from the
trusted inputs. A locally recomputed self-hash cannot launder a changed issue,
workflow, source, acceptance, result, or evidence binding.

A review registry binds the exact candidate-manifest SHA-256. Because the
manifest binds all exact shard bytes, that one digest closes the complete set.
Reviews cover every request exactly once in request-ID order and bind each
candidate digest. Each entry either:

- records `defer`, with null reason and rationale; or
- records `permanently_unavailable` with reason
  `source_ref_permanently_unavailable` and rationale
  `accepted_immutable_source_ref_unavailable_without_archive`.

Review authority comes from protected-branch review of the manifest, shards,
and registry plus a successful exact-input verification. JSON cannot
self-assert a reviewer. The registry schema is
`schemas/public-replay-unavailability-reviews-v1.schema.json`.

## Finalization

Finalization requires the same complete trusted input set and repeats the exact
rederivation before reading decisions:

```console
python scripts/prepare_public_replay_unavailability.py finalize \
  --inventory /frozen/inventory.json \
  --resolution-requests /frozen/resolution-requests.json \
  --results-root /frozen/results \
  --aggregate evidence/historical-public-replay-github-evidence-ba5f578.json \
  --workflow-registry configuration/public-replay-workflow-definitions-v1.json \
  --legacy-adjudication-registry configuration/public-replay-legacy-adjudications-v1.json \
  --candidate-manifest /reviewed/manifest.json \
  --candidate-shards /reviewed/shards \
  --reviews /reviewed/reviews.json \
  --output /new/dispositions.json
```

One deferred request keeps the review incomplete. Even a complete review stays
`blocked_on_state_contract_and_append_authorization`: a disposition is neither
a State event, queue item, replay result, nor corpus-completion claim. The
disposition binds the exact manifest and candidate-identity digests, total
candidate request/result counts, and terminal/deferred request/result counts.
The runtime disposition validator requires the exact manifest and shards: it
rejects a truncated or re-authored `complete` artifact, checks every terminal
entry against its exact candidate digest and ordered result IDs, and recomputes
the deferred complement. The
`schemas/public-replay-unavailability-dispositions-v1.schema.json` enforce the
same closed fields and status/claim relationships.

An existing final artifact can be reverified mechanically against the same
frozen inputs, reviewed candidate bytes, and exact review registry by replacing
`finalize` above with `verify-disposition`, replacing `--output` with
`--dispositions`, and otherwise supplying the same arguments. Verification
requires byte-for-byte equality with a fresh deterministic finalization.

## Remaining State boundary

The current historical State queue contains only authorized, qualified replay
work. It must not invent an execution profile or enqueue an unavailable source
merely to make `replay.unavailable` reachable. A separately reviewed State
contract must consume a finalized disposition without creating a fictional
submission, archive, execution profile, or attempted replay. That contract and
append authorization remain later gates.
