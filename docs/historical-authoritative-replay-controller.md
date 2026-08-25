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
  worktree paths, validates the qualification schema, and binds the selected
  matrix entry semantically;
- selects only the lexicographically first queue task and carries its attempt
  forward without resetting it;
- prepares the ordinary `replay.started` shape without a submission or archive
  field, while refusing terminal-event construction until an executor result
  can bind the exact attempt;
- binds the existing `historical_public_runner_v1` handoff and exact public
  source archive; and
- reads State's distinct ASCII-escaped canonical JSON, orders events by State's
  authoritative `(occurred_at, event_id)` key, validates reconfiguration,
  re-enqueue, retry, terminal, and unknown-transition causality, and prepares a
  `runner_lost` recovery only after the seven-hour lease and only with an event
  ID that can causally follow the started event.

All outputs are create-only. The controller performs no network, Git write,
State write, Cloudflare, AWS, release, or publication operation.

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

Two additional typed blockers prevent known wedges:

- a selected Gist source produces `kind: blocked` with reason
  `historical_public_gist_source_adapter_not_implemented`; the v1 runner accepts
  only `github_repo`, and the resolved historical evidence includes 59
  Gist-backed groups that cannot be silently treated as repositories;
- a task that has already consumed three attempts produces `kind: blocked`
  with reason `historical_public_attempt_limit_reached`.

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
checks for a running attempt, and produces only an ephemeral source-free
blocked plan in runner scratch. The plan is not uploaded because it retains
State-derived provenance. Public step summaries report only that the lane
remained dark; they do not reveal queue or recovery disposition. The workflow
requests no State write, OIDC,
Cloudflare, AWS, registry, Results, release, intake, or publication credential.

Cancellation before planning changes nothing. A future mutating controller
must append `replay.started` by exact-head compare-and-swap before execution,
use an attempt-bound executor result to append one terminal event afterward,
and on a lost or cancelled runner allow
the next serialized invocation to prepare the stale `runner_lost` recovery.
The current read-only workflow deliberately stops before all three writes.
