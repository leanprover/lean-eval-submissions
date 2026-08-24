# Authenticated legacy-result owner API

This API anchors immutable schema-version-2 records from the Results Git store
in private State and lets their authenticated owners add field-provenanced
production metadata. It never rewrites a Results record, changes its stable
result ID, or reinterprets its grandfathered solution-publication policy.

The implementation is bound to private State contract commit
`fa4fe8f0e74d66130e5f8671b05cc708e77c4b1f`. Before an owner operation, the
Worker resolves protected State `main`, proves that it equals or descends from
that commit, and checks the exact reviewed event schema, targeted-index
schemas, materializer, result-owner index builder, validator, and contract
documentation blob IDs. Successful proofs are cached across requests in one
Worker isolate under a content-addressed repository/head/contract key. The LRU
cache contains at most 64 completed proofs and never caches a promise,
credential, response, or failed proof. The transaction then uses that same
protected-main head as its compare-and-swap base. Contract drift fails closed
before a write.

## Safe configuration

Both environments track these non-secret variables:

```text
LEGACY_RESULT_OWNER_API_ENABLED=false
RESULT_OWNER_STATE_CONTRACT_COMMIT=fa4fe8f0e74d66130e5f8671b05cc708e77c4b1f
```

The route exists only when the enable flag is exactly `true` and the contract
commit is exact. The reviewed merge keeps both staging and production flags
false. Enabling this owner-only API does not enable submission intake:
production `INTAKE_ENABLED` remains independently false. OAuth start/callback
may operate while intake is disabled only when the owner API gate is enabled;
submission routes remain disabled.

Local integration sequencing note: commit
`fa4fe8f0e74d66130e5f8671b05cc708e77c4b1f` is the unmerged State contract
anchor. The local rollback qualification temporarily uses that same value as
`state_main_commit` so this branch is internally testable. Before this branch
is pushed, rebind that qualification field to the actual protected State
`main` commit produced when the contract lands. The runtime contract anchor may
remain `fa4fe8f0e74d66130e5f8671b05cc708e77c4b1f` only if that exact commit lands
unchanged and every pinned blob remains exact; otherwise refresh every contract
pin and proof listed below.

## Authentication and requests

The routes use the existing one-hour GitHub browser session. Bearer sessions
bind non-browser clients; cookie-authenticated mutations additionally require
the exact same-origin `Origin` check. Every mutation requires a canonical
lowercase UUIDv7 `Idempotency-Key`, which is the proposed immutable State event
ID.

Claim one historical record:

```http
POST /api/v1/results/claims
Idempotency-Key: <uuidv7>
Content-Type: application/json

{"result_id":"r2_<64 lowercase hex>","results_commit":"<40 lowercase hex>"}
```

The server derives `results/<authenticated-lowercase-login>.json`. Before it
fetches that path, both the provider and bounded Results GitHub App authority
prove with GitHub's compare/merge-base response that the client commit is an
ancestor of the exact protected environment branch (`main` in production,
`staging-results` in staging). The protected branch response, comparison head,
base, and merge base must agree. The server then fetches only the requested
immutable commit, requires exactly one matching record, and recomputes:

- the schema-version-2 result ID from owner, verbatim model, problem, and
  statement revision;
- the RFC 8785 SHA-256 of the complete base record, including its historical
  publication fields; and
- the provider-neutral source-record identity over exact repository, commit,
  owner-derived path, and canonical-record digest.

The pinned State contract defines `src1_` as SHA-256 of that canonical tuple
without a domain prefix. Changing only the Worker would split the operational
index, so domain separation requires a versioned State-contract migration and
new cross-language vectors rather than an in-place change to `src1_`.

The complete historical record digest uses RFC 8785, including ECMAScript's
shortest round-trippable JSON number serialization. The language-neutral
`server/test/fixtures/result-owner-canonicalization-vectors-v1.json` freezes
floating-point spellings, negative zero, canonical bytes, and SHA-256. Unknown
grandfathered `production_metadata` fields remain part of the immutable base
digest, but verification bounds them before canonicalization: at most 32 KiB
canonical UTF-8, depth 16, 256 total nodes, 128 members per container, 16 KiB
per string, and 256 bytes per object key. The known historical publication
fields retain their stricter semantic limits.

It then atomically creates `result.claimed`, the shared result-identity guard,
the owner overlay, the immutable source-record index, the initial targeted
amendment view, and the initial targeted release-status view. The release
status is exactly `not_scheduled` with a null release-event marker. A modern
`result.recorded` write reserves the same identity-guard path and creates the
same two lifecycle views in its result transaction. When that transaction also
contains `release.scheduled`, its release-status view is exactly `scheduled`
and names the schedule event; otherwise it is `not_scheduled` with a null
marker. A claim can therefore never collide silently with a server result, and
every result authority begins with the complete targeted lifecycle indexes
required by State validation. The global policy is one immutable result
authority per deterministic result identity: whichever valid `claimed` or
`recorded` guard lands first wins. A later claim, a modern result after a claim,
or a second modern submission with the same owner/model/problem/revision tuple
cannot adopt or replace that authority.

Backfill one or more current metadata fields:

```http
PATCH /api/v1/results/<result_id>/metadata
Idempotency-Key: <uuidv7>
Content-Type: application/json

{"production_metadata":{"web_access":false,"notes":"..."}}
```

The partial object must be nonempty and uses the same closed, bounded metadata
contract as server submissions. The owner must equal the claim overlay owner.
The new event is caused by the overlay's exact current mutation event and is
committed atomically with a complete replacement overlay. The identity and
source-record guards remain byte-for-byte immutable.

## Retry and conflict behavior

Every attempt pins one State head and target-reads only the required paths. A
lost non-forced ref update discards the unpublished tree/commit, resolves the
new protected head, repeats contract and targeted preflight, and rebuilds the
causal mutation. No branch is rewound.

- An exact existing claim is a 200 idempotent success even after later
  backfills and when retried at a later request clock, but only when its
  authority event and all immutable bindings agree. Replay comparison uses the
  authority event's stored original `occurred_at`, never the retry clock.
- Re-claiming the same logical record at another reachable Results commit is a
  200 idempotent success. The first claim remains canonical; its immutable base
  event, commit binding, and source-record index are never rewritten or
  duplicated.
- A backfill whose requested values are already current is a 200 no-op.
- Replaying the same backfill event ID succeeds after later mutations when its
  immutable event, body, actor, and each field it wrote still agree with the
  field-level overlay provenance; it need not remain the overlay's latest
  mutation.
- A changed same-key body, partial/forged indexes, stale causal head, recorded
  result collision, or occupied event path returns 409.
- A missing claim or different owner returns the same 404 response.
- A claim whose tuple is already reserved by `result.recorded`, a modern result
  whose tuple is reserved by `result.claimed`, or a duplicate modern tuple
  returns terminal `result_identity_conflict` 409. This is deliberately not a
  retryable callback failure: the first authority remains canonical, no second
  event/view is written, and operator reconciliation chooses a genuinely new
  statement revision or other corrected identity input instead of retrying the
  collision forever.
- Repeated owner-operation protected-main CAS loss returns 409. CAS exhaustion
  in lifecycle callbacks remains a retryable 503; State/provider unavailability
  and contract drift return 503. A protected Results head moving between the
  branch and ancestry reads is a distinct retryable 503. An uncertain State ref
  update is also `state_unavailable` 503 on every route; it is never reported as
  an internal 500 or an idempotency conflict.

Successful responses contain only `result_id` and a bounded status string.
They do not expose Results paths/commits/digests, historical source locators,
State commits, OAuth/session tokens, or private record fields. Structured error
logs contain stage and error class only.

## Enable and rollback gate

The first enablement has a zero-event migration precondition. At exact private
State `main` commit `82a036df052b4bd66f358b50925e939c862ee6f3`, inspected on
2026-08-24, the repository contained zero `result.recorded` events, zero
`result.claimed` events, and zero files under `views/result-identities/` (the
only event was `system.initialized`). Consequently no historical result guard
backfill is required. Missing guards are not repaired at runtime: an apparent
replay whose result event/view exists without its recorded guard fails closed
as State inconsistency. The live result-completion callback always enters this
repository replay check even when its submission view already names a result;
it never returns `already_recorded` solely from the view.

Immediately before changing either tracked enable flag to `true`, repeat these
read-only gates against the exact intended deployment inputs:

1. Validate production State and prove its protected `main` still descends from
   the pinned contract. If the three zero counts above changed before the first
   compatible deployment, stop and perform an explicit State migration/review.
2. Use the GitHub branch endpoint to prove `staging-results` exists, reports
   `protected: true`, and resolves to a full commit. The protected deployment
   workflow performs this read-only check before every staging Worker deploy;
   runtime verification repeats it for every owner claim.
3. Exercise claim, same-key replay at a later clock, metadata backfill, claim →
   modern-record collision, duplicate-modern-tuple collision, and unknown State
   update recovery in staging while intake and production owner routes remain
   disabled.
4. Enable staging only, record the first accepted owner mutation, and preserve
   its exact State commit as rollout evidence before considering production.

After the first result-owner event or guard exists, never roll back the Worker
to a commit that lacks these event decoders and identity-path reservations.
Disable the route with a forward deployment of this compatible implementation,
then repair or forward-deploy. State events and guards are append-only and are
not deleted during rollback. The tracked disabled configuration in this change
does not itself authorize either staging or production enablement.
