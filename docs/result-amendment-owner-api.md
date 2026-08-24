# Dark result amendment owner API

The submission Worker contains an authenticated, feature-disabled owner API for
the append-only result amendment contract at State commit
`fa4fe8f0e74d66130e5f8671b05cc708e77c4b1f`.

Both staging and production keep:

```text
RESULT_AMENDMENT_OWNER_API_ENABLED=false
RESULT_OWNER_STATE_CONTRACT_COMMIT=fa4fe8f0e74d66130e5f8671b05cc708e77c4b1f
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

The writer reads both targeted amendment and release-status documents at the
same protected State commit. It appends `result.retraction_requested` and
refreshes only `views/result-amendments/<prefix>/<result-id>.json` in one
non-forced compare-and-swap commit. The release-status document is required,
authority-bound, and deliberately left byte-for-byte unchanged: requesting a
withdrawal does not itself schedule, cancel, publish, or remove a release. A
pending repair, pending/approved/terminal retraction, stale mutation marker,
non-increasing UUIDv7, changed same-key request, missing or forged targeted
view, or repeated causal conflict fails closed. An exact same-key replay
returns the original revision without another write. Responses expose only
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
the same immutable `result.recorded` or `result.claimed` authority event. The
request must change the current effective problem tuple. Pending amendment
work, terminal retraction, a stale mutation marker, or a release status of
`running`, `published`, or `removed` returns 409. `not_scheduled`, `scheduled`,
`failed`, and `cancelled` states remain eligible for maintainer review before
another release run.

A successful transaction appends `result.problem_repair_requested` and
replaces only the targeted amendment view. It does not edit the release-status
view. Losing the compare-and-swap to a release transition or another amendment
restarts the complete protected-head read; the retry cannot append against a
stale release marker. Exact same-key replays are read-only successes, while a
changed body at an occupied event path is an idempotency conflict.

This route only records the owner's proposal. Applied/rejected repair decisions
still require a separately reviewed maintainer identity boundary and the exact
comparator evidence described by State.

## Temporary State binding and final rebind

`fa4fe8f0e74d66130e5f8671b05cc708e77c4b1f` is a local, unmerged State
contract anchor. Before any push or deployment, bind this branch to the landed
protected State commit and re-run qualification. The complete rebind surface is:

- `server/src/result-owner.ts`: `RESULT_OWNER_STATE_CONTRACT_COMMIT`;
- `server/src/github-state.ts`: every path/blob pair in
  `RESULT_OWNER_CONTRACT_BLOBS`, including the release-status schema and
  materializer;
- `server/wrangler.jsonc` for both environments and the generated
  `server/worker-configuration.d.ts`;
- both exact contract checks in `.github/workflows/deploy-worker.yml`;
- `.audit/cloudflare-rollback-qualification-v1.json`, including
  `state_main_commit` and the regenerated callback-contract digest;
- `docs/legacy-result-owner-api.md`, this document, and every exact fixture in
  `server/test/{api-v1,github-state,index}.test.ts`,
  `tests/test_validate_cloudflare_rollback.py`, and
  `tests/test_worker_deployment_workflow.py`.

Use
`rg --hidden --glob '!.git/**' -l 'fa4fe8f0e74d66130e5f8671b05cc708e77c4b1f'`
to prove that no temporary anchor remains, including the deployment workflow
and audit qualification. Separately compare every contract blob in the Worker
and its two proof tests against `git ls-tree` at the landed State commit. A
merge commit may retain the `fa4fe8f...` ancestor only when every pinned blob
is still exact; otherwise both the commit anchor and affected blob IDs must
change together.

## Operational invariants

- Production intake, replay, and publication remain independently disabled.
- Staging and production amendment flags remain false in tracked Wrangler
  configuration and rollback evidence.
- The Worker proves protected State main descends from the reviewed commit and
  verifies exact contract blobs before every owner write (with a bounded
  content-addressed proof cache).
- No AWS role, private source fetch, replay executor, or deployment is involved.
