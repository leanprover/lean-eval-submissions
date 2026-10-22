# Compute reporting

LeanEval measures the acceptance build separately from the submitter's account
of producing a proof. Generation token counts, elapsed time and estimated API
cost are optional, self-reported, per-problem totals including unsuccessful
attempts. Cached input is part of total input. Shared-run totals belong in
notes. Billing mode describes how the submitter paid; `cost_usd` is the estimated
API-equivalent cost, including for subscription users. Existing API payloads
remain valid; `cached_input_tokens` is the only added production metadata key.

## Acceptance measurement

The toolchain Lake comparator invokes an explicit trusted bubblewrap adapter. Its
`build` phase includes building the solution and exporting it; its `checker`
phase measures nanoda and Lean’s own checker together. Challenge preparation,
dependency download, root/tool compilation, GitHub queue time and source fetch
are excluded. Solution outputs start cold; restored dependency outputs are
reused. Imported Lean Pool modules without cached outputs remain part of the
submission's build cost. These are observational acceptance measurements, not
benchmarks on identical physical hardware.

`measure_acceptance_phase.py` reuses the replay counter implementation outside
bubblewrap. It counts userspace retired instructions across descendants and uses a
monotonic wall clock. Unavailable counters retain an explicit reason and never
become zero. The trusted metrics directory is outside writable submission paths.
Two successful build/export invocations and two successful checker invocations
are required before publishing totals. Persistence errors invalidate all phase
totals and preserve the wrapped command's exit status.

The evaluator publishes only reduced numeric data. The protected recorder binds
it to the exact accepted source, result ID, benchmark revision and workflow.
Sticky resubmissions of a different source cannot replace statistics for the
original accepted source. `acceptance-measurements` artifacts retain these
strict source-free receipts for 90 days.

`POST /api/v1/results/<result_id>/measurements` accepts only the lifecycle
callback token used in source-free jobs. It verifies the immutable accepted
record on the environment's protected Results branch and appends a deterministic
`result.measurement_recorded` event. This uses the existing callback credential
boundary; it does not introduce OIDC or credentials into the evaluator. Schema
version 7 of the public State projection carries measurements and dated metadata
updates. Projection versions 1–6 retain their existing public schema bytes and
omit the new cached-input field.

The measurement series includes the toolchain Git commit (which locks the bundled
comparator, exporter and checkers), the exact bubblewrap binary SHA256, architecture,
runner class and cache policy. `namespace-ubuntu-24.04-amd64-16x32-lake-v1` identifies
the current acceptance runner and phase adapter; bump it when those semantics change. The exact
workflow commit and run/attempt are retained as evidence. Wall time is visibly
marked **very noisy**. Instruction counts also depend on architecture and setup.
A failed measurement does not change proof acceptance or its immutable credit.

## Owner editor

Solution links open `/metadata/?result_id=…&results_commit=…`. GitHub sign-in
resolves the authenticated owner's exact accepted record and current metadata.
The editor preserves unrelated fields, uses the existing legacy claim/backfill
and server-submission amendment APIs, and sends `If-Match` against the current
mutation revision. Legacy CAS retries recheck that revision. Stale edits fail;
they never overwrite a newer owner update. Browser idempotency keys are issued
using the server's clock. The existing restrictions on retracted, pending-repair
and publication-conflicting legacy mutations still apply.

## Recovery and historical coverage

`reconcile-acceptance-measurements.yml` retries retained recorder receipts every
six hours. It executes no downloaded code, validates ZIP entries and receipt
identity, and posts only to the fixed production endpoint. A failed callback job
can also be rerun directly. Missing or expired artifacts are explicitly reported
for historical backfill; they are not treated as measurements.

Produce a complete public coverage report with:

```sh
python scripts/compute_reporting_coverage.py \
  --results-root /path/to/lean-eval-submissions \
  --public-state /path/to/public-state-v7.json \
  --output /tmp/compute-coverage.json
```

Existing historical queue tasks and terminal measurements are reused. Finish
legacy issue coverage with the existing historical inventory/final-delta planners
and public/private controllers. For server results predating instrumentation,
`plan_compute_backfill.py` creates a create-only event packet from that coverage
report, State's privately materialized `domain.json`, and a reviewed profile map
keyed by the **original benchmark commit**. A map entry contains `toolchain`,
`qualified: true`, `execution_profile_digest`, and `measurement_config_digest`.
Do not use fixture profiles: the original toolchain and exact executor image must
already have passed the existing qualification procedure. Missing qualification
is recorded as blocked; the planner never substitutes a newer benchmark.
Validate the proposed events with State before appending them. It does not append,
decrypt, dispatch, enable ordinary replay, or alter the historical replay gates.

## Rollout order

1. Merge the production and staging State contracts. Verify both exact contract
   commits and the new event schema before enabling their writers.
2. Merge/deploy the compatible submission Worker and promoted dispatch ref.
   Its rollback compatibility manifest must name those State contracts and the
   exact callback implementation. Keep the existing source-fetch/sandbox probes.
3. Merge/deploy the leaderboard consumer requesting public projection v7.
4. Verify one genuine accepted submission exposes build time and a measured or
   explicitly unavailable counter, and an authenticated owner can amend metadata
   without losing existing fields. Confirm unauthorized editing is denied.
5. Drain the existing historical queues and qualify/run any remaining exact
   server profiles. Re-run coverage until recoverable rows have a measurement or
   a recorded terminal failure. Source-unavailable rows retain their reasons.

The local implementation and automated tests do not constitute production
qualification or evidence that the historical replay backlog has been drained.
