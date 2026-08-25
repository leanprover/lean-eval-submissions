# Model identity producer API

The submission Worker contains the bounded authenticated producer for model
identity requests, decisions, aliases, and renames already defined by
protected State and consumed by the leaderboard. The producer is deliberately
dark in staging and production. Source completeness for these four operations
is not launch qualification for them or for the whole model-identity lifecycle.

## Authority and routes

The owner routes are:

- `POST /api/v1/model-identities` with `{ "display_name": ... }`;
- `POST /api/v1/model-identities/<mi1_...>/aliases` with `{ "alias": ... }`;
- `PUT /api/v1/model-identities/<mi1_...>/name` with
  `{ "display_name": ... }`.

The maintainer route is
`POST /api/v1/model-identities/<mi1_...>/decisions`. Approval accepts only
`{ "decision": "approve" }`; rejection additionally requires one registered
lowercase `reason_code`. All mutations require a canonical UUIDv7
`Idempotency-Key`.

No request body can select an owner or reviewer. Owner actors come only from a
signed session containing a canonical lowercase login and positive numeric
account ID. The session is minted either after GitHub OAuth `/user` verifies
that pair or after the existing agent lane verifies the owner's secret gist,
prescribed tag, and exact source commit and receives that pair from GitHub.
Maintainer authority requires the exact `(github_id, login)` pair to appear in
the closed environment configuration. Either numeric-ID or login drift fails
closed as not found. The OAuth access token is discarded after `/user`; the
callback remains bound to its exact environment URL, signed state cookie, and
one-use State nonce. The two issuers produce the same closed, signed,
one-hour `browser_session` contract; there is no unsigned actor path.

## State and collision binding

The Worker derives `mi1_...` and `ma1_...` with the same domain-separated
SHA-256 formulas and identifier vectors as protected State. It reads only the
targeted identity, alias, and immutable request/decision/mutation events. Each
CAS commit atomically writes the immutable event and its exact operational
view. Alias keys are permanent owner-scoped reservations; pending, rejected,
or consolidated identities fail closed. The whole State contract is ancestry-
and root-tree-bound to `MODEL_IDENTITY_STATE_CONTRACT_COMMIT` before a write.

Consolidation is deliberately not a Worker route. Protected State permits a
target identity to be consolidated later, which changes `resolved_model_id`
for every transitive predecessor identity and every alias that reaches it. A
target-only Worker commit would therefore disagree with the immutable graph.
Every consolidation-shaped path returns 404 under every owner/maintainer gate
combination. Health records the typed capability state
`requires_protected_reverse_impact_index`. Completing this operation requires
a protected State reverse-impact index and an atomic producer able to
rematerialize the complete affected graph; an unbounded Worker scan or an
arbitrary repository-size ceiling is not an acceptable substitute.

The conservative synchronous ceiling is 171 external subrequests: nine CAS
attempts, each reserving four snapshot/contract requests, nine targeted reads,
two Git object writes, and four requests for uncertain reference-update
recovery. The tracked intake Workers set `limits.subrequests` to 400, above this
route and the existing 369-request result-repair ceiling but below the current
Workers Paid default. This still exceeds the Workers Free plan's 50-request
allowance; see the current
[Workers limits](https://developers.cloudflare.com/workers/platform/limits/).
Model identity
mutations never run from Cron. Scheduled dispatch keeps its separate explicit
400-request application budget and remains a no-op for ordinary intake while
intake is disabled.

## Independent dark gates and launch work

`MODEL_IDENTITY_OWNER_API_ENABLED` and
`MODEL_IDENTITY_MAINTAINER_API_ENABLED` are independent exact-`true` gates.
The maintainer gate additionally requires a nonempty, closed
`MODEL_IDENTITY_MAINTAINERS` list, and both gates require the exact reviewed
`MODEL_IDENTITY_STATE_CONTRACT_COMMIT`. Both environments track both gates as
`false` and the identity list as `[]`. Health and rollback validation expose
only the two booleans and the public 171-request bound; configured identities
are never exposed.

Before enabling either gate, operators must separately record all of:

1. protected deployment accepts and preserves the tracked 400-subrequest limit
   on the paid Workers plan, and a dark maximal-contention test stays within the
   171-request model-identity bound;
2. environment-specific OAuth Apps retain the byte-exact HTTPS callback, least
   `read:user` scope, token expiry, and approved organization/temporary owner,
   and the agent issuer retains exact secret-gist owner, prescribed-tag, and
   source-commit binding;
3. the intended maintainer numeric-ID/login pairs are reviewed out of band and
   installed only in the matching protected environment;
4. dark staging proves OAuth state-cookie binding, nonce single use, exact
   OAuth- and agent-issued session identity, owner request, maintainer approve
   and reject, alias, rename, idempotent retry, collision, and cross-owner
   denial;
5. rollback qualification is regenerated for the exact deployed callback
   contract and both gates remain false throughout the qualification; and
6. a separate rollout decision explicitly enables the selected route family.

None of those live checks is established merely by merging this source.
Consolidation additionally remains blocked on its protected State prerequisite
and is not enabled by either existing feature gate.
