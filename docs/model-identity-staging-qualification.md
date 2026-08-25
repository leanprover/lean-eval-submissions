# Model identity persistent dark staging qualification

This source defines the staging-only controller, persistent harness, and
recovery protocol for the dark gates in
[the owner API contract](model-identity-owner-api.md). It has not been
dispatched and does not establish live qualification. The reviewed live fixture
manifest and its source-pinned digest are deliberately absent, so an acquisition
fails before any State read or write. Every job in both workflows requires the
deliberately impossible source ref
`refs/source-disabled/model-identity-qualification`, which GitHub never emits.
Merging this source therefore cannot run the qualification even if credentials
are installed, and cannot enable either public API, create a repository gate,
authorize a deployment, or mutate staging State. Arming requires a later
reviewed source commit that pins one exact verified fixture manifest after all
identity, seeding, deployment, and recovery prerequisites below exist.
The ordinary Worker deployment configuration has no qualification secret,
Durable Object, executor service binding, or private-service deployment step;
the two private Wrangler configurations are source-only inputs until that later
arming change.

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
Qualification, automatic/manual recovery, and protected-main Worker deployment
share the repository-wide `submission-worker-main` concurrency lock. A deploy
therefore cannot replace the exact `DEPLOYED_COMMIT` executable while its
durable staging journal remains active, and recovery cannot be overtaken by a
queued deploy after runner loss or cancellation.

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
atomically acquire a staging-only verification lease, verify all reviewed
fixture blobs at the exact protected head, recheck that head, and activate a
Durable Object journal keyed by the original workflow run ID. The journal ID
binds the complete acquisition, including the fixture ID and manifest digest.
Every step first persists an immutable plan containing fresh UUIDv7 event IDs,
timestamps, exact requests, and expected State changes. Sessions are never
persisted. Every successful step advances the journal and stores a durable
receipt. The controller calls `status` followed by `restore` from a `finally`
block on success, assertion failure, HTTP failure, and partial progress. A
separate source-disabled recovery
workflow is both manually invokable and triggered by completion of the
qualification workflow, so a later armed version can recover after cancellation,
runner loss, or controller process death using only the harness token and run ID.

Mutation recovery is reconcile-only: it never invokes the API request again.
No State movement releases a pending plan; an exact non-empty prefix of planned
commits is independently verified and atomically recorded as a recovered
receipt; any foreign movement remains untouched and fails closed. A retry of a
completed or reconciled step can return only its stored receipt after the same
role and actor authentication. The restore contract must reject a foreign State
commit, name the exact live parent commit and tree, create a new fast-forward
audit commit, prove the ref now names that commit, and prove its complete tree
equals the captured initial tree.
It must never force-move a ref. Recovery is idempotent when the durable journal
already records an exact restoration. The controller reports failure if
restoration is absent or malformed, and reports the combined failure class if
both qualification and recovery fail. It always verifies disabled public health
after a successful restoration, including when the qualification itself failed.
The qualification workflow preserves a canonical source-free JSON evidence
artifact containing its complete intent, journal, accepted proof responses,
contention measurement, restoration parent/head/tree evidence, errors by phase,
and final health without containing credentials. Standalone recovery preserves
its own source-free artifact containing the recovered journal,
restoration parent/head/tree evidence, recovery errors, and final health; it does
not reproduce the qualification intent or proof responses.

The mutation kernel is not exposed as a public bypass. The coordinator calls a
staging-only private service-bound executor with a signed, short-lived
capability that binds the journal, revision, operation, plan digest, request
digest, and request index. The executor invokes the same `apiRequest` kernel as
the dark public route and reports measured State subrequests, Git object writes,
and compare-and-swap attempts. A second private service-bound collision Worker
creates and verifies seven same-tree contenders before forwarding each original
ref update, so the eighth attempt is a genuine successful compare-and-swap.
Neither private Worker has a public route, preview URL, or production binding.

## Live-only prerequisites

The persistent harness remains intentionally **unarmed**. A reviewed source
change must pin one exact 148-document staging fixture manifest and its digest.
That manifest must bind the protected repository/ref/contract, exact seed parent
and one atomic seed commit, three distinct reviewed identities, the pending
rejection model, approved consolidation chain, exact 16+17 cap components, and
exact 16+16 contention components. Independent verification must cover every
canonical event, model view, alias view, reverse-impact view, content digest,
Git blob ID, causality edge, component membership, and manifest binding. Test
fixtures use a distinct `source_test_only` evidence class which the Python
runner refuses as live evidence.

Before dispatch, operators must also complete all of the following outside this
source branch:

- prove the paid staging Worker preserves the tracked 400-subrequest setting;
- configure required reviewers with self-review prevention on the
  `cloudflare-staging` environment, then review and install the harness
  credential only in the Worker staging runtime and that environment;
- obtain fresh one-hour sessions through the real byte-exact staging OAuth
  callback, the real agent secret-gist/tag/source-commit lane, and a separately
  approved participating cross-owner, plus a fresh real maintainer session;
  the OAuth and agent sessions must bind the same numeric-ID/login owner pair,
  the cross-owner and maintainer pairs must be distinct, and all five credentials
  including the harness token must be distinct;
- receive explicit authorization from each human whose identity or session is
  used; repository collaborator status alone is not consent and must not be
  converted into a fixture choice or credential;
- generate and independently review the exact fixture documents and manifest,
  create only its reviewed atomic staging seed commit, then pin the resulting
  fixture ID, seed commit/tree, and manifest digest in source;
- record and supply the intended maintainer numeric-ID/login pair without
  populating `MODEL_IDENTITY_MAINTAINERS` while the public APIs are dark;
- deploy the private collision Worker, private executor, and intake coordinator
  in dependency order, prove both private Workers have no public route and no
  production binding, and verify the coordinator still fails closed while the
  manifest is absent;
- quiesce unrelated staging State writers, independently reverify the exact
  seeded State head, review the protected-environment approval, and dispatch
  `.github/workflows/model-identity-staging-qualification.yml` from the exact
  deployed protected-main commit;
- regenerate rollback qualification for that exact deployed callback contract
  while proving both model-identity API gates remained false throughout;
- preserve the qualification and recovery artifacts, post-restore State commit,
  subrequest measurement, and disabled health evidence, then rotate/delete all
  five ephemeral credentials and remove the one-shot harness and workflow in a
  reviewed follow-up.

Do not mark any dark gate complete from source tests alone. Do not pin a fixture,
preseed State, deploy, remove the impossible source-ref gates, mint another
person's session, or dispatch without explicit identity participation and the
independent fixture review. Do not dispatch if the exact fourteen-step E2E,
failure-injection matrix, automatic and manual recovery, private service-binding
proof, quiescence proof, restoration review, credential rotation plan, required
environment approval, or exact rollback qualification is missing.
