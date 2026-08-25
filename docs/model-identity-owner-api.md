# Model identity producer API

The submission Worker contains the bounded authenticated producer for model
identity requests, decisions, aliases, renames, and consolidation already defined by
protected State and consumed by the leaderboard. The producer is deliberately
dark in staging and production. Source completeness for these operations
is not launch qualification for them or for the whole model-identity lifecycle.

## Authority and routes

The owner routes are:

- `POST /api/v1/model-identities` with `{ "display_name": ... }`;
- `POST /api/v1/model-identities/<mi1_...>/aliases` with `{ "alias": ... }`;
- `PUT /api/v1/model-identities/<mi1_...>/name` with
  `{ "display_name": ... }`; and
- `POST /api/v1/model-identities/<mi1_...>/consolidations` with
  `{ "target_model_id": "mi1_..." }`.

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
targeted identity, alias, immutable event, and bounded reverse-impact documents.
Before creating a model request, it proves that the exact immutable event path is
absent; an existing path is accepted only as the same fully validated request and
view, so reusing one UUIDv7 `Idempotency-Key` across route families cannot replace
an earlier event.
Every successful CAS commit writes its immutable event, exact operational
views, and the complete affected reverse-impact component atomically. Approval
creates the one-member component; alias and rename update it in the same commit.
Alias keys remain permanent owner-scoped reservations, and pending, rejected,
or already-consolidated source identities fail closed.

Consolidation reads the protected source and target component indexes. It
rejects self, missing, inactive, cross-owner, overlapping, malformed, or
greater-than-32-view unions. The source index names every transitive predecessor
identity and alias whose resolution changes; the Worker reads and cross-checks
every one, including strict timestamp and UUID append-authority ordering for
each causal predecessor, changes all of their `resolved_model_id` values,
records the source terminal's immutable consolidation event, deletes the old
source component, and installs the sorted complete union at the target in one
non-forced Git CAS.
The target may later be consolidated again. Exact retries therefore follow the
source view to its current terminal component instead of assuming the immediate
target remains terminal. Health records `atomic_reverse_impact_v1`.

Before any model write, production is ancestry- and exact-root-tree-bound to
protected State `6799522f7fe57263de4a66499e52ce4bfda69baa`; staging is bound
to portable mirror `9fc7c431a92c678554c65ebac68d3fddf4990d29`. The proof fixes the
complete README, docs, schema, and scripts entries that define and validate the
inductive reverse-impact contract. Each unseen State head is reproved; malformed
regular-file metadata, paths, component counts, member paths, member bindings,
or terminal bindings fail before a Git object is created. There is no repository
scan and no repository-size assumption.

The conservative synchronous ceiling is 400 external subrequests. Consolidation
uses at most eight CAS attempts, each reserving five
protected-branch/snapshot/contract requests,
32 member-document reads across the complete source and target components, two
component-index reads, five mutation/causal event reads, two Git object writes,
and four requests for uncertain reference-update
recovery. The tracked intake Workers set `limits.subrequests` to 400, exactly at this
route and the existing 369-request result-repair ceiling. Paid-plan preservation
and a live dark maximal-path measurement remain mandatory pre-enable gates; see the current
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
only the two booleans and the public 400-request bound; configured identities
are never exposed.

Before enabling either gate, operators must separately record all of:

1. protected deployment accepts and preserves the tracked 400-subrequest limit
   on the paid Workers plan, and a dark maximal-contention test stays within the
   400-request model-identity bound;
2. environment-specific OAuth Apps retain the byte-exact HTTPS callback, least
   `read:user` scope, token expiry, and approved organization/temporary owner,
   and the agent issuer retains exact secret-gist owner, prescribed-tag, and
   source-commit binding;
3. the intended maintainer numeric-ID/login pairs are reviewed out of band and
   installed only in the matching protected environment;
4. dark staging proves OAuth state-cookie binding, nonce single use, exact
   OAuth- and agent-issued session identity, owner request, maintainer approve
   and reject, alias, rename, complete-graph consolidation, later-chain retry,
   component-cap refusal, idempotent retry, collision, and cross-owner denial;
5. rollback qualification is regenerated for the exact deployed callback
   contract and both gates remain false throughout the qualification; and
6. a separate rollout decision explicitly enables the selected route family.

None of those live checks is established merely by merging this source. The
protected State prerequisite is present, but consolidation remains dark with
the rest of the owner surface until a separate credentialed rollout decision.

The [dark staging qualification scaffold](model-identity-staging-qualification.md)
defines a fail-closed journaled controller and independent recovery workflow for
those checks. Both are source-disabled and undispatched: their separately
reviewed staging-only harness, durable journal and lease, short-lived issuer
sessions, protected environment approval, quiesced State window, live
400-subrequest measurement, mandatory fast-forward restoration, and credential
cleanup remain prerequisites.
