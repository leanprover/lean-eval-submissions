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

Recovery fixtures must include the `system.initialized` root and complete
authority, qualification, enqueue, retry/reconfiguration, unavailable, and
ordinary modern-replay histories. Every positive reducer history must pass both
schema and semantic validation under the same State contract used by the
protected workflow. The workflow must also validate live protected State before
invoking the narrower projection; fixtures never substitute for that runtime
precondition.

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

Historical-public and generic/private replay share one limit of four total
execution attempts: the initial execution and at most three retries. Attempt
four is terminal, and an exhausted task remains in State without starving a
later eligible task.

## Five bounded production lanes

`.github/workflows/historical-authoritative-replay.yml` is manual, serialized
within the public lane, and dark while repository variable
`HISTORICAL_PUBLIC_REPLAY_CONTROLLER_ENABLED` is absent. When deliberately
enabled with the separately reviewed environment credentials, one invocation:

1. validates and materializes protected production State;
2. appends one stale `runner_lost` recovery and stops, stops on a live attempt,
   or plans the next exact eligible task;
3. renders and deploys only the task's qualified immutable image, then proves
   exact health while ordinary replay and staging acceptance remain disabled;
4. reserves the exact replay-task/attempt cleanup identity in durable storage,
   without State-write or Cloudflare-deployment authority;
5. fetches the exact public repository or Gist commit and benchmark commit and
   builds the deterministic handoff in a five-minute step, before any attempt
   becomes running; a noncancelled preparation failure or five-minute step
   timeout retains the old bounded `source_fetch_failed` attempt accounting;
6. refreshes validated State, requires the prepared task and all its reviewed
   bindings to remain identical, and checks execution capacity before each
   exact-head `replay.started` compare-and-swap; it then invokes the executor
   without a State writer or Cloudflare deployment credential in scope; and
7. appends exactly one attempt-bound terminal event, or a typed orchestration
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

On successful preparation, every start publication is preceded by a capacity
check that preserves the complete reviewed measurement wall limit (currently
330 minutes), five minutes of poll/delivery grace, twelve minutes for cleanup
and State publication, a 310-second executor-POST allowance, and sixty seconds
for State publication/request preparation/OIDC. The POST has an outer
300-second timeout with a five-second forced-kill grace, so its retries cannot
consume the unbounded final-request extension of curl's retry window. The
six-hour job allows 410 seconds of setup before State start. The job timestamp
is captured in the first shell step; GitHub's preceding job setup is not part
of that timestamp, so this is conservative operational accounting rather than
an exact measurement of the hosted job's entire lifetime.

If capacity is insufficient while the refreshed task is still queued, the run
reports successful `deferred` without starting an attempt or invoking an
executor. An enabled replenisher may launch one successor using the existing
strictly decreasing `remaining_runs` budget and unchanged baseline checks.
At most two consecutive warmed successors are permitted for the same exact
queued task. The dispatch carries that task identity and consecutive-deferral
counter; successful execution resets both. A third refusal fails the job while
the task remains queued, stopping for diagnosis. Every refusal emits a warning
and source-free elapsed/required/remaining summary, even in a single manual run.
Busy/unknown attempts, task changes, and State/rebind errors stop the chain.
Deferred runs consume the existing decreasing chain budget, never task attempts.
No infinite or silently green warm loop is introduced.

A preparation failure, including a noncancelled step timeout, instead publishes
the existing start/failure pair without the execution-capacity guard, because
no executor will run. `source_fetch_failed` remains retryable only through four
total attempts across deliberate retries. After that accounting the job fails,
retaining the old stop-chain behavior: an outage cannot automatically exhaust
attempts across the corpus. A manager diagnoses before deliberately retrying.
Operator cancellation never takes this fallback.

A second capacity check after request preparation and OIDC minting reserves the
POST allowance as well as the full measurement limit and grace. Both checks
print source-free elapsed/required/remaining seconds. A warning after the 202
receipt reports any unexpected job-deadline truncation of the process window.
The sixty-second State/start allowance is not a guarantee against an arbitrary
network stall: if State publication already succeeded and startup exceeds that
allowance, the existing `runner_start_failed` path records the failure. An
ambiguous executor start still requires destruction proof; cancellation still
uses seven-hour stale recovery. No measurement limit, checker outcome, image,
toolchain, or retry classification is changed.

Cloudflare deployment, State reads, State writes, public source fetching, and
executor OIDC are kept in separate steps. The lane has no AWS permission and
never reads an encrypted private archive. It uploads no State-derived plan,
source, request, or verdict artifact.

The temporary
`.github/workflows/historical-replay-two-lane-driver.yml` starts at most one
public lane and exactly four deterministic private shards. Each lane retains
its own non-cancelling concurrency group, so there is never more than one
controller active for the same lane or shard. A successful controller may
dispatch exactly one same-lane successor with a strictly decreasing run
budget. It stops replenishing on cancellation, job failure, a live same-lane
attempt, an empty queue, a blocked public queue, or budget exhaustion. The
driver defaults leave headroom above the initial 145 public and 562 private
non-v1 tasks and hard-limit every lane to 1,024 runs per start.

The driver binds the protected commit that starts the chain. Before every
successor dispatch and again at the beginning of every successor, the workflow
requires either that exact commit or a complete, non-truncated descendant whose
entire diff consists only of added or modified `results/*.json` files and newly
added `verification/r2_<64 lowercase hex digits>.json` evidence. Verification evidence present at the protected baseline cannot be changed.
Any code, workflow, deletion, rename, divergent history, or oversized comparison stops
the chain before replay authority is used.

Only the five-minute driver and replenishment jobs receive `actions: write`.
They have no checkout, protected environment, OIDC, or repository/service
secret. The long-running replay jobs retain their prior `contents: read` and
`id-token: write` permissions and do not receive dispatch authority.

Because both lanes append to one State repository, the public start append now
uses the same four-attempt compare-and-swap shape as the existing private
controller. Before each attempt it refreshes and validates State, rematerializes
the public queue, and permits only the queue's whole-State source count and
digest to change. Any change to the selected task, attempt, authority,
qualification, execution profile, measurement profile, or transition fails
closed. Terminal appends already refresh and retry against exact current State.

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

Implementation is not activation. Before creating the repository variables:

- merge the completion-plan amendment and this finite controller restoration;
- confirm every selected task already binds a reviewed qualification profile
  and State enqueue;
- revalidate the existing production State read/write keys, AWS unwrap role,
  and Cloudflare deployment credentials in the protected `replay-production`
  environment without exposing their values;
- confirm production State has no running historical replay and the current
  public and all four private planners each return an executable or empty plan;
- retain the prior accepted staging evidence and rerun repository tests for
  stale recovery, terminal idempotency, network isolation, and fail-closed
  health; do not create a new persistent qualification campaign.

Production intake, ordinary private replay, and release publication are
independent gates and do not become enabled by this controller.

A noncancelled preparation-step timeout is a failed outcome before continue-on-error, distinct from job cancellation; see [the Actions runner step implementation](https://github.com/actions/runner/blob/main/src/Runner.Worker/StepsRunner.cs#L295).

## Prepare the immutable executor before the execution job

A public pilot spent413 seconds deploying and waiting for the qualified image,
and494 seconds from its first shell step to executor invocation. That cold path
cannot fit the410-second setup allowance while retaining the full330-minute
runner limit, delivery grace and cleanup tail. Deferral is a safety stop rather
than a strategy for completing such a campaign.

`prepare-executor` now validates protected production State and plans read-only,
then deploys and strictly health-checks the exact qualified executor in a
separate thirty-minute job. It has no State writer, OIDC execution authority,
source archive, cleanup reservation, executor request or recovery append. A
busy, stale or empty plan never triggers deployment. An infrastructure failure
stops the workflow without starting or failing a task.

The following fresh six-hour `replay-one` job validates State again, handles the
existing busy/stale-recovery paths, and rederives the task and qualified inputs.
Before reservation it compares a source-free prepared execution hash and the
job SHA for consistency, then repeats exact health to bind the actual deployed
Worker commit. The SHA job output alone is not deployment evidence. The prepared
hash binds the entire validated plan except whole-State event count/digest,
which may change through unrelated private-lane appends. A changed task,
attempt, authority, image, profile, measurement or code fails closed. There is
no candidate/source artifact handoff and no repeated wrangler deployment in the
execution job. Both jobs remain under the same noncancelling workflow-level
public concurrency group.

The existing410-second setup guard remains in the actual execution job, with
full19800000ms evaluation,20100000ms process window and twelve-minute tail.
This split moves only pre-attempt image preparation outside that budget; it
adds no observer, idle-running State, asynchronous recovery policy or new
executor protocol. Root's next bounded staging/production packet must still
verify actual warm execution-job timings rather than assume all setup costs
have vanished.

A typed PUBLIC runtime mismatch or internal guard failure emits only a fixed
source-free notice and summary, fails execution, and explicitly prevents a
successor. Existing cleanup/destruction and truthful orchestration-failure
accounting remain unchanged. A retryable State reason is not permission to
silently retry a deterministic profile mismatch; diagnose the observed runtime
before a deliberate new attempt. No permanent-unavailability event is inferred
from a single guard failure.

As verified by the operator on 2026-11-20, replay-production currently enforces
branch policy with no required reviewers or wait timer. Both jobs retain that
protected environment; future protection changes must be respected. Execution
accepts current main only at the dispatch SHA or a completely proven Results-only
descendant using the existing closed comparison filter. Code changes, divergent
history, truncation, renamed or deleted files still stop before State mutation.
