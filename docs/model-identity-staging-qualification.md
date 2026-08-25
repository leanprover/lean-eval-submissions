# Model identity dark staging qualification scaffold

This source scaffold defines the staging-only controller and recovery protocol
for the dark gates in [the owner API contract](model-identity-owner-api.md). It
has not been dispatched and does not establish live qualification. Every job in
both workflows requires the deliberately impossible source ref
`refs/source-disabled/model-identity-qualification`, which GitHub never emits.
Merging this scaffold therefore cannot contact a harness even if credentials are
installed, and cannot enable either public API, create a repository gate,
authorize a deployment, or mutate staging State. Arming either workflow requires
a later reviewed source commit after the complete harness and recovery
prerequisites below exist.

The dormant qualification workflow is restricted to the exact protected `main`
commit in `leanprover/lean-eval-submissions`, the first workflow attempt, the
closed `kim-em` original and triggering actor, and stable GitHub account ID
`477956`, inside `cloudflare-staging`. It checks the exact deployed commit and
requires ordinary intake plus both model-identity API gates to remain disabled
before and after the run. Production is neither an input nor an endpoint. The
controller rejects redirects. Secrets are step-scoped after checkout. The
dedicated harness credential authenticates journal operations; each proof gets
only its required OAuth-owner, agent-owner, distinct-owner, or maintainer
session, and restoration receives no user session.

The internal harness response contract has fourteen independent proofs. Each
response binds the exact numeric-ID/login actor, route, credential role, HTTP
outcome, canonical event/model/alias identifiers, operation-specific assertions,
journal revision, preceding and resulting State commit and complete tree, and
disabled public gates. The controller refuses an aggregate success response,
missing or extra fields, unexpected State movement on a denial/retry proof, a
claimed mutation that does not advance both commit and tree, a State head or
journal revision that is not chained from the preceding response, or any
response reporting an enabled public API. It requires:

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

Restoration is not optional. Before the first State mutation, the harness must
atomically acquire a staging-only lease and create a durable journal keyed by
the original workflow run ID. Every step advances that journal. The controller
calls `status` followed by `restore` from a `finally` block on success, assertion
failure, HTTP failure, and partial progress. A separate source-disabled recovery
workflow is both manually invokable and triggered by completion of the
qualification workflow, so a later armed version can recover after cancellation,
runner loss, or controller process death using only the harness token and run ID.

The restore contract must reject a foreign State commit, name the exact live
parent commit and tree, create a new fast-forward audit commit, prove the ref now
names that commit, and prove its complete tree equals the captured initial tree.
It must never force-move a ref. Recovery is idempotent when the durable journal
already records an exact restoration. The controller reports failure if
restoration is absent or malformed, and reports the combined failure class if
both qualification and recovery fail. It always verifies disabled public health
after a successful restoration, including when the qualification itself failed.
Both workflows preserve a canonical source-free JSON evidence artifact; it
contains the complete intent, journal, proof responses, contention measurement,
restoration parent/head/tree evidence, errors by phase, and final health without
containing credentials.

## Live-only prerequisites

The privileged internal harness is intentionally **not** implemented by this
scaffold. A separate reviewed source change must implement the fixed fourteen
operations and the version-2 journal/status/restore protocol against a
disposable, quiesced staging State window. Its durable journal and lease must
survive Worker and GitHub runner loss. It must be unreachable in production by
construction, authenticate only a fresh `MODEL_IDENTITY_QUALIFICATION_TOKEN`,
bind the exact deployed commit, prove the exact initial/head/tree and journal
chain before every write, reject foreign concurrent commits, avoid forced ref
updates, and remain within the Worker request ceiling for every call. A partial
generic owner/maintainer bypass is not acceptable.

Before dispatch, operators must also complete all of the following outside this
source branch:

- prove the paid staging Worker preserves the tracked 400-subrequest setting;
- configure required reviewers with self-review prevention on the
  `cloudflare-staging` environment, then review and install the harness
  credential only in the Worker staging runtime and that environment;
- obtain fresh one-hour sessions through the real byte-exact staging OAuth
  callback, the real agent secret-gist/tag/source-commit lane, and a separately
  approved temporary cross-owner issuer, plus a fresh real maintainer session;
  the OAuth and agent sessions must bind the same numeric-ID/login owner pair,
  the cross-owner and maintainer pairs must be distinct, and all five credentials
  including the harness token must be distinct;
- record and supply the intended maintainer numeric-ID/login pair without
  populating `MODEL_IDENTITY_MAINTAINERS` while the public APIs are dark;
- quiesce unrelated staging State writers, independently capture the exact
  initial State head, review the protected-environment approval, and dispatch
  `.github/workflows/model-identity-staging-qualification.yml` from the exact
  deployed protected-main commit; and
- regenerate rollback qualification for that exact deployed callback contract
  while proving both model-identity API gates remained false throughout; and
- preserve the qualification and recovery artifacts, post-restore State commit,
  subrequest measurement, and disabled health evidence, then rotate/delete all
  five ephemeral credentials
  and remove the one-shot harness and workflow in a reviewed follow-up.

Do not mark any dark gate complete from source tests alone. Do not remove the
impossible source-ref gates or dispatch if the harness, durable journal,
automatic and manual recovery, quiescence proof, restoration review, credential
rotation plan, required environment approval, or exact rollback qualification
is missing.
