# Authenticated legacy-result owner API

This API anchors immutable schema-version-2 records from the Results Git store
in private State and lets their authenticated owners add field-provenanced
production metadata. It never rewrites a Results record, changes its stable
result ID, or reinterprets its grandfathered solution-publication policy.

The implementation is bound to private State contract commit
`a3081798468f8c364a5c7d619aee2fd83e2028e3`. Before an owner operation, the
Worker resolves protected State `main`, proves that it equals or descends from
that commit, and checks the exact reviewed event schema, targeted-index
schemas, materializer, result-owner index builder, validator, and contract
documentation blob IDs. The transaction then uses that same protected-main
head as its compare-and-swap base. Contract drift fails closed before a write.

## Safe configuration

Both environments track these non-secret variables:

```text
LEGACY_RESULT_OWNER_API_ENABLED=false
RESULT_OWNER_STATE_CONTRACT_COMMIT=a3081798468f8c364a5c7d619aee2fd83e2028e3
```

The route exists only when the enable flag is exactly `true` and the contract
commit is exact. The reviewed merge keeps both staging and production flags
false. Enabling this owner-only API does not enable submission intake:
production `INTAKE_ENABLED` remains independently false. OAuth start/callback
may operate while intake is disabled only when the owner API gate is enabled;
submission routes remain disabled.

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

The server derives `results/<authenticated-lowercase-login>.json`, fetches it
only at the requested immutable commit through the bounded Results GitHub App
authority, requires exactly one matching record, and recomputes:

- the schema-version-2 result ID from owner, verbatim model, problem, and
  statement revision;
- the RFC 8785 SHA-256 of the complete base record, including its historical
  publication fields; and
- the provider-neutral source-record identity over exact repository, commit,
  owner-derived path, and canonical-record digest.

It then atomically creates `result.claimed`, the shared result-identity guard,
the owner overlay, and the immutable source-record index. A modern
`result.recorded` write reserves the same identity-guard path, so a claim can
never collide silently with a server result.

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
  backfills, but only when its authority event and all immutable bindings agree.
- A backfill whose requested values are already current is a 200 no-op.
- Replaying the same event ID succeeds only when the current event, body,
  actor, and field-level overlay provenance agree.
- A changed same-key body, partial/forged indexes, stale causal head, recorded
  result collision, or occupied event path returns 409.
- A missing claim or different owner returns the same 404 response.
- Repeated protected-main CAS loss returns 409; State/provider unavailability
  and contract drift return 503.

Successful responses contain only `result_id` and a bounded status string.
They do not expose Results paths/commits/digests, historical source locators,
State commits, OAuth/session tokens, or private record fields. Structured error
logs contain stage and error class only.
