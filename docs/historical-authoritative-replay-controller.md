# Historical authoritative replay controller

Historical accepted public results have no modern submission UUID or encrypted
archive. State therefore materializes them in the distinct
`historical-public-replay-queue.json` version-2 view. They must never be passed
through the ordinary private replay queue by synthesizing either identity.

`scripts/historical_replay_controller.py` is the source-free controller
foundation for that separate queue. It:

- validates the complete closed queue task, stable task identity, ordering,
  retry state, optional profile-reconfiguration provenance, and explicit public
  source authority;
- loads the authority plan, qualification profile, profile matrix, and runner
  contract from their exact ancestor Git commits rather than trusting the HEAD
  worktree paths, validates the qualification schema, requires the qualified
  image source to exist in the controller source's ancestry, verifies its
  exact matrix and runner-contract blobs, and binds the selected matrix entry
  semantically;
- selects the lexicographically first eligible queue task, deterministically
  stepping over typed Gist-adapter and exhausted-attempt blockers without
  mutating or silently disposing of them, and carries the selected task's
  attempt forward without resetting it;
- prepares the ordinary `replay.started` shape without a submission or archive
  field, while refusing terminal-event construction until an executor result
  can bind the exact attempt;
- binds the existing `historical_public_runner_v1` handoff and exact public
  source archive; and
- requires the caller to complete authoritative State validation before it
  reads State's distinct ASCII-escaped canonical JSON, then defensively
  rechecks the historical transition subset it consumes in authoritative
  `(occurred_at, event_id)` order; this local reducer is not a replacement for
  State's complete schema or materialized-view validation;
- prepares a `runner_lost` recovery only after the seven-hour lease and only
  with an event ID that can causally follow the started event.

All outputs are create-only. The controller performs no network, Git write,
State write, Cloudflare, AWS, release, or publication operation.

The recovery fixtures include the required `system.initialized` root and full
authority, qualification, enqueue, retry/reconfiguration, unavailable, and
ordinary modern-replay histories. They were validated against the production
State contract at `6799522f7fe57263de4a66499e52ce4bfda69baa`; positive reducer
histories pass both State schema and semantic validation. The workflow still
validates the live protected State checkout before invoking the narrower
projection, so the fixture coverage is evidence rather than a substitute for
that runtime precondition.

## Explicit transport blocker

The deployed authoritative executor accepts the modern encrypted-archive
request. The qualified historical image instead accepts the distinct public
runner handoff. Reusing the private endpoint would erase this authority
boundary, so every nonempty controller plan contains:

```json
{
  "status": "blocked",
  "reason": "historical_public_executor_not_implemented",
  "required_contract": "historical_public_executor_v1"
}
```

The handoff binder emits the same blocker after validating the exact source
archive. A later implementation must add and qualify that distinct executor
transport before any workflow may append `replay.started`. Removing the string
or merely setting a repository variable is not enablement evidence.

Two additional typed blockers expose unsupported queue work before a
`replay.started` event can be prepared:

- a selected Gist source produces `kind: blocked` with reason
  `historical_public_gist_source_adapter_not_implemented`; the v1 runner accepts
  only `github_repo`, and the resolved historical evidence includes 59
  Gist-backed groups that cannot be silently treated as repositories;
- a task that has already consumed three attempts produces `kind: blocked`
  with reason `historical_public_attempt_limit_reached`.

These blockers do not provide terminal-disposition semantics. The controller
scans the already sorted, State-validated queue and selects the first task with
neither blocker, so a Gist-backed or attempt-exhausted task cannot starve later
eligible work. Blocked tasks remain untouched in State. When every remaining
task is blocked, the controller emits `kind: blocked` for the first task rather
than pretending the queue is empty. Live queue revalidation repeats the same
eligibility selection before a `replay.started` event can be constructed.

The existing v1 handoff and verdict omit attempt. Consequently the controller
does not expose terminal/failed State-event CLI modes, and its terminal-event
builder fails with `historical_public_attempt_binding_not_implemented`. A
future executor contract must bind replay task, attempt, handoff, and verdict
end to end before terminal construction can be enabled.

## Dark read-only workflow

`historical-authoritative-replay.yml` is manual, serialized, and gated on the
absent `HISTORICAL_PUBLIC_REPLAY_CONTROLLER_ENABLED` variable. Even if the gate
is deliberately set later, the current workflow checks out production State
with a separately provisioned read-only key, validates and materializes it,
passes the explicit State-validated precondition to the narrower recovery
projection, checks for a running attempt, and produces only an ephemeral
source-free blocked plan in runner scratch. The plan is not uploaded because it
retains State-derived provenance. Public step summaries report only that the
lane remained dark; they do not reveal queue or recovery disposition. The
workflow requests no State write, OIDC, Cloudflare, AWS, registry, Results,
release, intake, or publication credential.

Cancellation before planning changes nothing. A future mutating controller
must append `replay.started` by exact-head compare-and-swap before execution,
use an attempt-bound executor result to append one terminal event afterward,
and on a lost or cancelled runner allow
the next serialized invocation to prepare the stale `runner_lost` recovery.
The current read-only workflow deliberately stops before all three writes.
