# Dark result amendment owner API

The submission Worker contains an authenticated, feature-disabled owner API for
the append-only result amendment contract at State commit
`4b8dcdf0a3d03749f51bef23807eeb1d00c43b72`.

Both staging and production keep:

```text
RESULT_AMENDMENT_OWNER_API_ENABLED=false
RESULT_OWNER_STATE_CONTRACT_COMMIT=4b8dcdf0a3d03749f51bef23807eeb1d00c43b72
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

Browser cookies are accepted only on same-origin mutations. The Worker derives
the next positive consecutive revision; callers cannot choose it or a causal
parent. It resolves the exact result identity guard, immutable authority event,
legacy overlay or modern submission view, current targeted amendment view, and
current mutation event at one protected State commit. The authenticated login
must equal the authority-derived owner. Missing and wrong-owner results both
return 404.

The writer appends `result.retraction_requested` and refreshes only
`views/result-amendments/<prefix>/<result-id>.json` in one non-forced
compare-and-swap commit. A pending repair, pending/approved/terminal retraction,
stale mutation marker, non-increasing UUIDv7, changed same-key request, forged
view, or repeated causal conflict fails closed. An exact same-key replay returns
the original revision without another write. Responses expose only result ID,
revision, and status; owner login, reason, private comparator evidence, source
locators, and State commit are not returned.

Approval, rejection, maintainer override, and terminal retraction are not
implemented here. The existing lifecycle callback bearer token identifies an
automation client, not a human maintainer, and cannot safely authenticate the
public `reviewer_login` required by State. A future maintainer API needs a
reviewed identity/role boundary and separate least-privilege feature gate.

## Problem repair request: intentionally unavailable

The authenticated route and closed request decoder exist at:

```http
POST /api/v1/results/<r2_id>/problem-repairs
```

When the dark feature gate is enabled in tests it returns
`503 {"error":"repair_state_unavailable"}` without a State write. State forbids
a repair request once release is running, published, or removed. Its current
targeted amendment view does not carry release status and is absent before a
result's first amendment; the submission view also does not track result
release state. A full event-tree scan would be an expensive new trust boundary,
so the Worker does not guess.

The next State contract must provide a protected-main-derived, targeted,
immutable view for every result that binds at least:

- result ID, authority event, owner, declared model, base/effective problem
  tuple, and shared mutation marker;
- current repair and retraction revisions/pending state;
- current release status and the release event proving it; and
- exact materializer/schema/validator parity suitable for a credentialed CAS
  writer.

Only after that contract is independently reviewed and pinned may the repair
route construct `result.problem_repair_requested`.

## Operational invariants

- Production intake, replay, and publication remain independently disabled.
- Staging and production amendment flags remain false in tracked Wrangler
  configuration and rollback evidence.
- The Worker proves protected State main descends from the reviewed commit and
  verifies exact contract blobs before every owner write (with a bounded
  content-addressed proof cache).
- No AWS role, private source fetch, replay executor, or deployment is involved.
