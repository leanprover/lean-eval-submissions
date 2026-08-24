# Dark result amendment owner API

The submission Worker contains an authenticated, feature-disabled owner API for
the append-only result amendment contract at State commit
`163e9314c881493e08d23baf35ff40456f9c2331`.

Both staging and production keep:

```text
RESULT_AMENDMENT_OWNER_API_ENABLED=false
RESULT_OWNER_STATE_CONTRACT_COMMIT=163e9314c881493e08d23baf35ff40456f9c2331
```

The gate is independent of submission intake and the legacy claim/backfill
gate. A disabled route returns 404 before authentication, provider access, or a
State read. Enabling it later requires an exact State contract pin; this change
does not authorize that enablement.

## Owner retraction request

```http
POST /api/v1/results/<r2_id>/retractions
Authorization: Bearer <owner session>
Idempotency-Key: <lowercase UUIDv7>
Content-Type: application/json

{"reason_code":"owner_requested_withdrawal"}
```

The UUIDv7 timestamp may not be ahead of the Worker clock. The recorded
mutation time retains millisecond precision. Exact occupied-event replay is
checked before the writer's `event_id > mutation_event_id` rule, so a valid
historical retry remains idempotent while a new stale/future key or same-second
timestamp truncation cannot wedge the strict causal order.

Browser cookies are accepted only on same-origin mutations. The Worker derives
the next positive consecutive revision; callers cannot choose it or a causal
parent. It resolves the exact result identity guard, immutable authority event,
legacy overlay or modern submission view, current targeted amendment view, and
current mutation event at one protected State commit. The authenticated login
must equal the authority-derived owner. Missing and wrong-owner results both
return 404.

The writer reads both targeted amendment and release-status documents at the
same protected State commit. A non-initial release status must name an exact
immutable system event whose type and result identity agree with that status.
Every request, decision, override, and terminal event named by the amendment
view is also read and rebound to the corresponding sub-state; the latest marker
alone is not treated as proof of the whole view.
It appends `result.retraction_requested` and
refreshes only `views/result-amendments/<prefix>/<result-id>.json` in one
non-forced compare-and-swap commit. The release-status document is required,
authority-bound, and deliberately left byte-for-byte unchanged: requesting a
withdrawal does not itself schedule, cancel, publish, or remove a release.
Owner retraction requests remain allowed in every release status so a published
result can enter the reviewed removal path. A
pending repair, pending/approved/terminal retraction, stale mutation marker,
non-increasing UUIDv7, changed same-key request, missing or forged targeted
view, or repeated causal conflict fails closed. An exact same-key replay
returns the immutable request's original revision without another write even
after a later compatible maintainer decision moves the targeted head. Responses expose only
result ID, revision, and status; owner login, reason, private comparator
evidence, source locators, release state, and State commit are not returned.

Approval, rejection, maintainer override, and terminal retraction are not
implemented here. The existing lifecycle callback bearer token identifies an
automation client, not a human maintainer, and cannot safely authenticate the
public `reviewer_login` required by State. A future maintainer API needs a
reviewed identity/role boundary and separate least-privilege feature gate.

## Owner problem repair request

The authenticated route and closed request decoder exist at:

```http
POST /api/v1/results/<r2_id>/problem-repairs
Authorization: Bearer <owner session>
Idempotency-Key: <lowercase UUIDv7>
Content-Type: application/json

{
  "corrected_problem_id":"two_plus_three",
  "corrected_statement_revision":2,
  "reason_code":"wrong_problem_revision"
}
```

The Worker derives owner authority and the next consecutive repair revision;
the caller cannot choose a parent or revision. Both targeted documents must
exist, decode under their closed schemas, name the requested result, and bind
the same immutable `result.recorded` or `result.claimed` authority event. Any
non-initial release status must also bind its exact immutable system release
marker. The request must change the current effective problem tuple. Pending amendment
work, terminal retraction, a stale mutation marker, or a release status of
`running`, `published`, or `removed` returns 409. `not_scheduled`, `scheduled`,
`failed`, and `cancelled` states remain eligible for maintainer review before
another release run.

A successful transaction appends `result.problem_repair_requested` and
replaces only the targeted amendment view. It does not edit the release-status
view. Losing the compare-and-swap to a release transition or another amendment
restarts the complete protected-head read, so it cannot append against a release
view superseded by a concurrent atomic release transition. The runtime proves
the named immutable marker but does not global-scan State for an unindexed later
release event; safety also relies on the pinned State contract's atomic
release-event/status-view transaction and validator. Exact same-key replays are read-only successes, while a
changed body at an occupied event path is an idempotency conflict.

This route only records the owner's proposal. Applied/rejected repair decisions
still require a separately reviewed maintainer identity boundary and the exact
comparator evidence described by State.

## Protected State binding

The final binding is protected production State `main`
`163e9314c881493e08d23baf35ff40456f9c2331`. Every amendment, owner-index,
release-status, materializer, and schema blob introduced by the reviewed
amendment contract is unchanged at that commit. The later append-authority and
status-view work expanded only the validator used by this proof; its exact blob
is `0b4c876475fcc9c9d5cf6269c800509530673bb4`. Runtime, deployment, rollback,
and test fixtures bind that same commit and closed blob set. Any future State
advance must refresh them together and requalify before an owner write.

## Operational invariants

- Production intake, replay, and publication remain independently disabled.
- Staging and production amendment flags remain false in tracked Wrangler
  configuration and rollback evidence.
- The Worker proves protected State main descends from the reviewed commit and
  verifies exact contract blobs before every owner write (with a bounded
  content-addressed proof cache).
- The live result-completion callback creates the initial amendment and release
  views, so it intentionally performs the same complete 15-blob proof even
  while both owner gates are dark. A State contract advance must be paired with
  a compatible Worker pin advance or result completion fails closed with 503.
- No AWS role, private source fetch, replay executor, or deployment is involved.
