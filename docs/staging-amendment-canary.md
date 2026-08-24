# Staging amendment apply/reject canary

This source-qualified canary closes the collision-index staging gate without
temporarily enabling either public amendment API. The manual workflow runs only
from protected `main`, enters `cloudflare-staging`, and uses only that
environment's readiness credential. It has not been dispatched.

The internal route refuses every runtime except the exact staging State
repository and configuration. Ordinary intake must be disabled, the existing
staging promotion canary must be active, both amendment gates must be false,
and the maintainer list must be the empty canonical array. Production is not a
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

The workflow binds the dispatch actor to `kim-em`, the reviewer recorded in the
two canary decisions. That operator supplies the exact deployed commit, initial
staging State head, and four strictly ordered UUIDv7 event IDs. Retrying a
partial run requires the refreshed current State head and the same event IDs;
the already-written operations are then read-only. Once either fixture has a
canary request, a different request identity is rejected, making the route
one-shot even if its readiness credential remains installed.

After merge, first dark-deploy the exact protected-main commit and recheck live
health. Generate and retain the four UUIDv7 values, read the current staging
State head, and dispatch `.github/workflows/staging-amendment-canary.yml` with
confirmation `APPLY_AND_REJECT_STAGING_FIXTURES`. Preserve the run ID, exact
commit, final State commit, both reservation paths, and post-run disabled health
in the infrastructure ledger. Do not mark the tracker gate complete unless all
four operations and the final disabled-health proof pass.
