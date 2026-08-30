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

## Reviewed retained baseline and current packet

The complete source-free review registry for the exact `ba5f5784427621f8b9be7396dd45a0938792707d`
Results baseline is
`evidence/public-replay/unavailability-review-registry-v1/b2187b1ec749087ed532bec3216f7f31c7fdf97a2a84a05e19cb69aac117757a.json`.
It covers all 187 retained requests exactly once and adopts the documented
`source_ref_permanently_unavailable` reason with the
`accepted_immutable_source_ref_unavailable_without_archive` rationale for all
439 bound Results. The corresponding deterministic source-free disposition is
`evidence/public-replay/unavailability-dispositions-v1/afe3c3d1f8657ee3f7c6bad05fc72f5a5d6f8f0a609f25fdce35c8d0edcc3321.json`.

The current protected evidence extends that reviewed set without replacing it.
For Results commit `844ade95c0a432e63a84798f84969b8d9f2f53a3`, the exact
current inputs have inventory digest
`b17c24071e3945ceb1b0e8fe492b90e868a89a064d8ae2cd033b7f787ec27780`,
resolution-request digest
`b12d436e03ed6fe2af29f9ac04b05498570ce117610302dd10aa890183c56840`,
workflow-registry digest
`f9e3f39683cce17cbb8389e6ab78a5fa7443ed36ee11f1ab0cb96ba7be9da747`,
and aggregate digest
`7c10dfc3e3d66f6f9ae0107ef2ed94b8f731d7f8410741ed3f5978dc55e149e5`.
The current content-addressed packet is:

- candidate manifest
  `evidence/public-replay/unavailability-candidate-bundle-v1/010be7d30736574043c5db3cfdbbb671f466f880633e4f2fb9a188c8bbed585d.json`,
  binding seven adjacent shards;
- review registry
  `evidence/public-replay/unavailability-review-registry-v1/bb6ecc3bd701266069bd89b9748e96ea66121c4ff1c3d1d5d8a423ea66005b5b.json`;
  and
- final disposition
  `evidence/public-replay/unavailability-dispositions-v1/e577802df7df3a657a1dbfea20d60985264cf82bc955e39951160acf39adc66b.json`.

It reviews 195 requests / 459 Results. The exact prior 187 requests / 439
Results remain identical at the request/result-identity layer and already have
terminal State events. The current-only delta is eight requests / 20 Results.
A State append consuming this packet must append only that disjoint 20-Result
delta; it must not replace or duplicate the prior 439 events.

Neither packet represents the final issue-intake cutoff. Every later acceptance
still requires its own inventory, classification, and terminal disposition. A
complete current review also does not itself authorize a State append, create
replay work, claim replay execution, or establish corpus completion. Those
claims remain false in the disposition artifact.

## State append boundary

The existing root `historical_result.replay_unavailable` contract is the
smallest State mechanism for this packet. It consumes the finalized disposition
without creating a fictional submission, archive, execution profile, attempted
replay, release, or queue item. Before appending the 20 current-only events, the
producer must reverify the exact protected submissions commit and packet bytes,
prove that current protected State contains exactly the prior 439 event subjects
and none of the 20 new subjects, validate the complete materialized State graph,
and advance protected State by compare-and-swap. The historical replay queues
remain restricted to authorized, profile-qualified replay work.
