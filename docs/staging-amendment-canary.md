# Staging amendment apply/reject canary

This source-qualified canary closes the collision-index staging gate without
temporarily enabling either public amendment API. The manual workflow runs only
from protected `main`, enters `cloudflare-staging`, and uses a dedicated
one-shot `STAGING_AMENDMENT_CANARY_TOKEN`. The general readiness credential
cannot invoke this route. It has not been dispatched.

The internal route refuses every runtime except the exact staging State
repository and configuration. Ordinary intake must be disabled, the existing
staging promotion canary must be active, both amendment gates and the legacy
owner gate must be false, intake mode must be exactly `disabled`, and the
maintainer list must be the empty canonical array. Production is not a
workflow input or endpoint, and the production runtime fails the route guard
before authentication or State access.

The target table is closed:

- Apply `r2_99df81809318fd2673d82da042b451f77b55606c6b506beb4526828ee1e7079e`
  from `two_plus_two` revision 1 to `list_append_singleton_length` revision 1.
  Comparator evidence is recomputed from protected staging Results commit
  `972178d59e2b3c5300baa728a1356f0d49dafb87`. The final read must find permanent
  reservation `eri1_362e69696a5c468d0482086b6eb3f24d68dea6b4795284a017096b092a800775`
  bound to that result and decision event.
- Reject the same candidate tuple for
  `r2_3f28ce10fd9bad352dc29394254ec7c414b57269757c3488cd108bd544186423`.
  The final read must still find candidate reservation
  `eri1_b1f3167cd78dcdcef990d5b09ae447bdf3e470f60236c6a2be2009a260a6127a`
  absent.

This is deliberately a synthetic collision-index exercise, not a claim that
the fixture proof solves the corrected challenge. The fixture source proves
`two_plus_two`; it does not prove `list_append_singleton_length`. Comparator
evidence binds the protected Results record and both same-group benchmark
manifests, but does not establish proof compatibility. Both records are
staging-only withheld fixtures and must never be interpreted as correctness or
production publication evidence.

The workflow binds the dispatch actor to `kim-em`, the reviewer recorded in the
two canary decisions. The exact four UUIDv7 event IDs, their matching
millisecond timestamps, request/decision links, targets, and outcomes are
compiled into the Worker; the caller supplies only the exact deployed commit,
initial staging State head, operation name, and confirmation. Each mutation is
bound to the supplied State head. An exact already-written event is a read-only
success after ambiguous response loss, but an absent event never rebases onto a
different head. The final amendment view and candidate reservation are read
from one exact post-mutation snapshot, whose commit is chained into the next
operation. Retrying a partial run requires the refreshed current State head;
the immutable already-written operations remain read-only.

After merge, install the same fresh random `STAGING_AMENDMENT_CANARY_TOKEN` as
a staging Worker secret and a `cloudflare-staging` environment secret. Then
dark-deploy the exact protected-main commit and recheck live health. Read the
current staging State head and dispatch
`.github/workflows/staging-amendment-canary.yml` with
confirmation `APPLY_AND_REJECT_STAGING_FIXTURES`. Preserve the run ID, exact
commit, final State commit, both reservation paths, and post-run disabled health
in the infrastructure ledger. Do not mark the tracker gate complete unless all
four operations and the final disabled-health proof pass.

After recording successful evidence, rotate or delete the dedicated credential
from both locations and remove the one-shot route and workflow in a reviewed
follow-up deployment. Keep the permanent staging events and reservation; older
production rollback qualifications remain accepted through their exact
historical callback file set.
