# Model identity producer API

The submission Worker provides authenticated model-identity owner and
maintainer routes backed by protected State. These routes are independently
gated from submission intake and are dark in both tracked environments.

## Launch routes and authority

The owner surface is:

- `POST /api/v1/model-identities` with `{ "display_name": ... }`;
- `POST /api/v1/model-identities/<mi1_...>/aliases` with
  `{ "alias": ... }`; and
- `PUT /api/v1/model-identities/<mi1_...>/name` with
  `{ "display_name": ... }`.

The maintainer surface is
`POST /api/v1/model-identities/<mi1_...>/decisions`. Approval accepts only
`{ "decision": "approve" }`; rejection additionally requires one registered
lowercase `reason_code`. All mutations require a canonical UUIDv7
`Idempotency-Key`.

No request body can select an owner or reviewer. Owner actors come from a
signed session after the existing OAuth or verified-agent authentication lane.
Maintainer authority requires the exact `(github_id, login)` pair from the
signed session to appear in the closed environment configuration. Numeric-ID
or login drift and non-owner access fail closed as not found. Cookie-authenticated
mutations remain same-origin.

The Worker derives model and alias identifiers with the same domain-separated
hashes as State. Immutable events and operational views are committed together
with non-forced compare-and-swap updates. Reusing an idempotency key for a
different operation, alias collisions, malformed State, and ownership mismatch
fail closed.

Production and staging writes require their exact reviewed
`MODEL_IDENTITY_STATE_CONTRACT_COMMIT`. The tracked Workers retain the current
400-subrequest ceiling. That ceiling is a configuration fact, not a requirement
for a maximal-contention or persistent qualification campaign.

## Independent gates

`MODEL_IDENTITY_OWNER_API_ENABLED` and
`MODEL_IDENTITY_MAINTAINER_API_ENABLED` are independent exact-`true` launch
gates. Both require the exact environment-specific State contract. The
maintainer gate also requires a nonempty, closed `MODEL_IDENTITY_MAINTAINERS`
list. Health reports their effective booleans without exposing configured
identities.

The implemented consolidation route has a third gate,
`MODEL_IDENTITY_CONSOLIDATION_API_ENABLED`, which is effective only when the
owner gate and exact State contract are also effective. All three flags remain
`false` in tracked staging and production configuration. Consolidation remains
disabled, is not part of this launch, and must not be exercised as a launch
test.

## Bounded launch smoke

The completion-plan smoke for this surface is limited to:

1. verify health reports the owner, maintainer, and consolidation gates
   disabled;
2. enable only the selected staging owner gate and prove alias and rename
   success, alias-collision denial, and non-owner denial;
3. enable only the selected staging maintainer gate with the reviewed identity
   pair and prove one decision succeeds while a non-maintainer is denied; and
4. verify the independent gate and health states, then disable the enabled gate
   and confirm the corresponding routes are dark again.

Consolidation, later-chain consolidation, component-cap stress,
maximal-contention measurement, and rollback-qualification regeneration are
not launch gates.
