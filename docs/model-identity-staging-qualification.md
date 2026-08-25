# Model identity dark staging qualification scaffold

This manual source scaffold defines the protected, staging-only controller for
the dark gates in [the owner API contract](model-identity-owner-api.md). It has
not been dispatched and does not establish live qualification. Merging it does
not enable either public API, install a schedule, create a repository gate, or
authorize a deployment.

The workflow runs only from the exact protected `main` commit in
`leanprover/lean-eval-submissions`, for the closed `kim-em` dispatch actor, and
inside the existing `cloudflare-staging` environment. It checks the exact
deployed commit and requires ordinary intake plus both model-identity API gates
to remain disabled before and after the run. Production is neither an input nor
an endpoint. The controller rejects redirects and sends the dedicated harness
credential and three short-lived sessions only to the byte-exact staging
origin.

The internal harness response contract has fourteen independent proofs. The
controller refuses an aggregate success response, missing or extra fields,
unexpected State movement on a denial/retry proof, a State head that is not
chained from the preceding response, or any response reporting an enabled
public API. It requires:

1. the exact OAuth-issued owner session identity;
2. the exact agent-issued session identity for the same owner;
3. an owner request;
4. maintainer approval;
5. maintainer rejection;
6. alias assignment;
7. rename;
8. complete reverse-impact graph consolidation;
9. an exact retry after the target is consolidated again;
10. refusal of a greater-than-32-view union;
11. an immutable-event idempotent retry;
12. refusal when that UUIDv7 event is reused by another route family;
13. denial from a distinct signed owner session; and
14. a live eight-attempt maximal-contention measurement at or below 400
    external subrequests.

Restoration is not optional. After the initial health preflight succeeds, the
controller calls the harness `restore` operation from a `finally` block on
success, assertion failure, HTTP failure, and partial progress. The restore
contract must create a new fast-forward audit commit whose complete tree equals
the supplied initial staging State commit; it must never force-move a ref. The
controller reports failure if restoration is absent or malformed, and reports
the combined failure class if both the qualification and restoration fail.
The final health check runs only after exact restoration succeeds.

## Live-only prerequisites

The privileged internal harness is intentionally **not** implemented by this
scaffold. A separate reviewed source change must implement the fixed fourteen
operations and the restore protocol against a disposable, quiesced staging
State window. It must be unreachable in production, authenticate only a fresh
`MODEL_IDENTITY_QUALIFICATION_TOKEN`, prove the exact initial/head chain before
every write, reject foreign concurrent commits, avoid forced ref updates, and
remain within the Worker request ceiling for every call. A partial generic
owner/maintainer bypass is not acceptable.

Before dispatch, operators must also complete all of the following outside this
source branch:

- prove the paid staging Worker preserves the tracked 400-subrequest setting;
- review and install the harness credential only in the Worker staging runtime
  and the existing protected `cloudflare-staging` environment;
- obtain fresh one-hour sessions through the real byte-exact staging OAuth
  callback, the real agent secret-gist/tag/source-commit lane, and a separately
  approved temporary cross-owner issuer; the OAuth and agent sessions must bind
  the same numeric-ID/login owner pair and all three tokens must be distinct;
- record the intended maintainer numeric-ID/login pair out of band without
  populating `MODEL_IDENTITY_MAINTAINERS` while the APIs are dark;
- quiesce unrelated staging State writers, independently capture the exact
  initial State head, review the protected-environment approval, and dispatch
  `.github/workflows/model-identity-staging-qualification.yml` from the exact
  deployed protected-main commit; and
- regenerate rollback qualification for that exact deployed callback contract
  while proving both model-identity API gates remained false throughout; and
- preserve the run, post-restore State commit, subrequest measurement, and
  disabled health evidence, then rotate/delete all four ephemeral credentials
  and remove the one-shot harness and workflow in a reviewed follow-up.

Do not mark any dark gate complete from source tests alone. Do not dispatch if
the harness, quiescence proof, restoration review, credential rotation plan, or
protected-environment approval is missing.
