# Historical authoritative replay controller

Historical accepted public results have no modern submission UUID or encrypted
archive. State materializes them in the distinct version-2
`historical-public-replay-queue.json`; the controller never invents a private
submission or archive identity for them.

`scripts/historical_replay_controller.py` validates the complete queue task and
loads the authority plan, qualification profile, profile matrix, and runner
contract from their exact ancestor Git commits. It selects the first eligible
task, preserves its retry count, constructs a causally ordered `replay.started`,
binds the exact source archive and attempt into
`historical_public_executor_v1`, and accepts a terminal verdict only when the
executor confirms sandbox destruction. All controller outputs are create-only;
the controller itself performs no network, Git write, State write, Cloudflare,
AWS, release, or publication operation.

## Source adapters and executor boundary

Both reviewed public source forms are implemented without changing the runner
handoff:

- `github_repo` uses a detached exact-commit GitHub checkout and the repository
  identity validator in `historical_public_runner.py`.
- `gist` uses `historical_public_gist_source_adapter.py`, which verifies the
  exact reviewed owner, Gist ID, commit, tree, and remote before emitting the
  same deterministic archive and `historical_public_runner_v1` handoff.

The recovery fixtures include the required `system.initialized` root and full
authority, qualification, enqueue, retry/reconfiguration, unavailable, and
ordinary modern-replay histories. They were validated against the production
State contract at `b0a30e3a64aa5c05660040405b32135dea4b7f1d`; positive reducer
histories pass both State schema and semantic validation. The workflow still
validates live protected State before invoking the narrower projection, so the
fixtures are evidence rather than a substitute for that runtime precondition.

The executor request closes over the runner nonce, replay task, attempt,
handoff and archive digests, reviewed execution and measurement digests, and
qualified image digest. The dedicated Worker route is independently gated by
`HISTORICAL_PUBLIC_REPLAY_ENABLED`; ordinary private replay remains disabled in
the generated historical executor. A terminal State event requires the exact
request/verdict pair and `destruction: confirmed`.

The Worker start is idempotent and returns a running receipt. The workflow then
polls a source-free status request carrying the same complete execution
identity, minting a fresh short-lived OIDC token for each bounded poll. Exact
terminal receipts are replayable, while command-RPC and sandbox-destruction
failures remain retryable until the executor can durably confirm destruction.

The only remaining typed planning blocker is the reviewed three-attempt limit.
An exhausted task remains in State and cannot starve a later eligible task.

## Serialized production workflow

`.github/workflows/historical-authoritative-replay.yml` is manual, serialized,
and dark while repository variable
`HISTORICAL_PUBLIC_REPLAY_CONTROLLER_ENABLED` is absent. When deliberately
enabled with the separately reviewed environment credentials, one invocation:

1. validates and materializes protected production State;
2. appends one stale `runner_lost` recovery and stops, stops on a live attempt,
   or plans the next exact eligible task;
3. renders and deploys only the task's qualified immutable image, then proves
   exact health while ordinary replay and staging acceptance remain disabled;
4. reserves the exact replay-task/attempt cleanup identity in durable storage,
   without State-write or Cloudflare-deployment authority, before appending
   `replay.started` by exact-head compare-and-swap;
5. fetches the exact public repository or Gist commit and benchmark commit,
   builds the deterministic handoff, and invokes the executor without a State
   writer or Cloudflare deployment credential in scope; and
6. appends exactly one attempt-bound terminal event, or a typed orchestration
   failure. The orchestration-failure append is explicitly guarded by
   `!cancelled()`: cancellation after start leaves the running attempt untouched
   and converges only through the seven-hour stale recovery on the next
   serialized invocation. `runner_start_failed` remains the failure phase while
   preparing the executor request and minting its first token. Immediately
   before the first executor POST can escape, the workflow records
   `runner_lost` and an attempted-start marker. A lost, rejected, malformed, or
   otherwise ambiguous start response therefore cannot append `replay.failed`
   until the source-free cleanup route consumes the exact durable reservation
   and returns an attempt-bound `destruction: confirmed` proof. Polling and
   cancellation recovery use the same cleanup identity and idempotent proof.

Cloudflare deployment, State reads, State writes, public source fetching, and
executor OIDC are kept in separate steps. The lane has no AWS permission and
never reads an encrypted private archive. It uploads no State-derived plan,
source, request, or verdict artifact.

The dedicated historical-production OIDC audience accepts protected `main`
only on the four historical routes: start, status, cleanup reservation, and
cleanup. Every route binds the exact repository, production environment,
workflow ref, token SHA, and workflow SHA. Start, status, and cleanup
reservation additionally require the exact deployed commit. Cleanup is the
sole exception: a later protected-main revision may invoke that destructive,
source-free route so a cancelled runner can consume its existing exact
task/attempt reservation after `main` advances. Ordinary replay and staging
remain immutable-dispatch-tag only.

## Activation gate

Implementation is not activation. Before creating the repository variable:

- commit and authorize every required qualification profile and State enqueue;
- provision the production State read/write keys and Cloudflare deployment
  credentials only in the protected `replay-production` environment;
- deploy and qualify the exact historical executor code and receipt protocol;
- run one accepted staging/public canary through the complete State lifecycle;
- verify stale recovery, terminal idempotency, and fail-closed health.

Production intake, ordinary private replay, and release publication are
independent gates and do not become enabled by this controller.
