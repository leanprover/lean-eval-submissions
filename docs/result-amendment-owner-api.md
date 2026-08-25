# Dark result amendment owner and maintainer APIs

The submission Worker contains an authenticated, feature-disabled owner API for
the append-only result amendment contract at production State commit
`b0a30e3a64aa5c05660040405b32135dea4b7f1d` and the independently reviewed
staging State contract `9fc7c431a92c678554c65ebac68d3fddf4990d29`.

Both staging and production keep:

```text
RESULT_AMENDMENT_OWNER_API_ENABLED=false
RESULT_AMENDMENT_MAINTAINER_API_ENABLED=false
RESULT_AMENDMENT_MAINTAINERS=[]
RESULT_OWNER_STATE_CONTRACT_COMMIT=<9fc7c431… in staging; 714f7408… in production>
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

Approval, rejection, maintainer override, and terminal retraction use a
separate, fail-closed maintainer identity boundary and independent dark feature
gate. The tracked false value does not enable any of those routes. The existing
lifecycle callback bearer token still identifies only an automation client and
never grants human maintainer authority; equivalent paths under `/internal/`
do not exist.

`RESULT_AMENDMENT_MAINTAINERS` is a bounded closed JSON array of exact GitHub
numeric ID/lowercase-login pairs. Both values must match the authenticated
session, and IDs and logins must each be unique. The tracked value is the empty
array. Rollback validates the exact configuration but deliberately records only
the supported/enabled booleans: the allowlist is never copied into health,
rollback plans, prestate evidence, or workflow summaries.

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

This route only records the owner's proposal. Application or rejection requires
the separately authenticated maintainer route below.

## Maintainer problem repair decision

```http
POST /api/v1/results/<r2_id>/problem-repairs/decisions
Authorization: Bearer <maintainer session>
Idempotency-Key: <lowercase UUIDv7>
Content-Type: application/json

{"decision":"apply","results_commit":"<40 lowercase hex>"}
```

A rejection instead has the exact body
`{"decision":"reject","reason_code":"insufficient_comparator_evidence"}`.
The two forms cannot be mixed. The route authenticates the exact numeric-ID and
lowercase-login pair before any State or comparator read. For application it
reads the pending targeted view, verifies the named Results commit is reachable
from the configured protected environment branch, re-fetches and hashes the exact public Results blob,
selects one canonical immutable record, and binds the base and corrected
benchmark manifests at that record's benchmark commit. A separate read-only
broker authority first proves that benchmark commit is an ancestor of protected
`leanprover/lean-eval` `main`. This constant-repository authority uses only
strictly allowlisted public anonymous reads and does not expand either GitHub
App installation or permission set. Both manifests must
name the requested tuple and have the same problem group.

The State writer then independently checks the pending request, causal order,
release barrier, immutable owner/model/base tuple, corrected tuple, and exact
comparator fields. It recomputes both `ch1_…` challenge IDs and the complete
comparator binding SHA-256 before constructing an event. Application derives
the closed `eri1_…` path for the corrected `(owner, model, problem, revision)`
tuple and reads that one permanent reservation in the same pinned State
snapshot. A reservation held by another stable result is a permanent
collision. An absent reservation is created in the same transaction as
`result.problem_repaired`; a reservation already held by the same result
permits a historical revisit and is left unchanged. A rejection appends
`result.problem_repair_rejected` without reading or writing a candidate
reservation. Both decisions update only the targeted amendment view in the
same non-forced compare-and-swap commit. Exact
same-key replay is read-only; changed decision material, forged derived
evidence, a missing pending request, or `running`, `published`, or `removed`
release status fails before any write. Responses contain only result ID,
revision, and status.

The collision decision never consults a materialized aggregate or scans the
repository. Modern result recording and legacy claiming create or confirm the
base-tuple reservation atomically with the result authority. Pending requests
and rejected repairs do not reserve their proposed tuple. The protected State
validator derives the permanent reservation set from immutable authority and
applied-repair events; staging's two existing authorities were migrated to
exact reservations, while production's empty authority set required none.
This remains a dark implementation, not an enablement claim: a separately
authorized staging apply/reject canary and the pre-enable checks below remain
required.

## Maintainer retraction decisions

The dark maintainer API also exposes:

```text
POST /api/v1/results/<r2_id>/retractions/decisions
POST /api/v1/results/<r2_id>/retractions/override
POST /api/v1/results/<r2_id>/retractions/finalize
```

The decision body is an exact `approve` or `reject` plus one bounded reason
code. Override is the maintainer-only path for an unavailable owner and cannot
impersonate a GitHub actor. Finalization accepts an empty object, requires an
approved decision or override, repeats that decision's original reviewer in
the terminal State evidence regardless of which authorized maintainer submits
the finalization, and derives
`not_published`, `removal_required`, or `already_removed` from the exact pinned
release status. A running release blocks finalization. Each transition has one
strict causal parent, monotonically increasing event identity/time, exact
idempotence, and a redacted response. The public reviewer login remains in the
State event as required review evidence but is not returned by the API.

## Protected State binding

The production binding is protected State `main`
`b0a30e3a64aa5c05660040405b32135dea4b7f1d`; staging is independently bound to
its reviewed contract commit `9fc7c431a92c678554c65ebac68d3fddf4990d29`.
Both contracts contain release-status schema version 2 and permanent
effective-result reservations, but their repository-specific migration
evidence and current graphs make their root subtree IDs intentionally
different. Runtime, tracked configuration, rollback checks, and test fixtures
bind the exact commit for each repository. Any future State advance must
refresh the corresponding proof and requalify before an owner write.

## Operational invariants

- Production intake, replay, and publication remain independently disabled.
- Staging and production owner and maintainer amendment flags remain false in
  tracked Wrangler configuration, health checks, and rollback evidence; the
  tracked maintainer identity list is empty.
- The Worker proves protected State main descends from the repository's
  reviewed commit and checks its current non-recursive root tree contains
  exactly one correctly typed and hash-bound `README.md`, `docs`, `schema`, and
  `scripts` entry. Those four entries bind the complete reviewed contract
  subtrees; changed, missing, duplicate, and wrong-type entries fail closed.
  The proof costs one root-tree call at the exact contract head or one ancestry
  plus one root-tree call at a descendant, and uses a bounded content-addressed
  proof cache.
- The live result-completion callback creates the initial amendment and release
  views and base effective-identity reservation, so it performs the same root
  proof even
  while both owner gates are dark. A State contract advance must be paired with
  a compatible Worker pin advance or result completion fails closed with 503.
- The worst external-subrequest mutation is a repair application under maximum
  State contention. The closed bound is 369 GitHub requests: at most 28 for the
  initial targeted graph read, 8 for protected Results and benchmark comparator
  verification, and 9 complete writer attempts (attempts 0 through 8) at at
  most 37 requests each, including uncached ancestry/root proof, all bounded
  historical event reads, the candidate reservation and its immutable
  provenance event on a same-result revisit, tree/commit creation, duplicate
  ref-update recovery, and reachability proof. Other owner and maintainer mutations omit
  the comparator, reservation read, or both. Before enablement, the deployed
  Workers plan must provide a per-request external-subrequest allowance of at
  least 369; a 50-subrequest free plan is insufficient. The tracked gates stay
  false if this prerequisite is not proved.
- No AWS role, private source fetch, replay executor, or deployment is involved.
