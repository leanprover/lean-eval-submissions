# Replay orchestrator and disposable-VM interface

This Wave 2 foundation is local-only. It plans public-source replay and checks
the result returned by a disposable runner. It does not install a GitHub
workflow, invoke a production execution backend, decrypt an archive, mint a
capability, write State, or publish a result.

## Locked inputs

The orchestrator consumes one canonical `replay-queue.json` materialized by
State plus two reviewed configuration objects:

- the execution profile pins the VM image by SHA-256, CPU model, architecture,
  kernel release, cache state, measurement command, Lean/Go/Rust toolchain
  versions, and exact commits of comparator, landrun, lean4export, and nanoda;
- the measurement configuration pins memory and wall-time limits and the exact
  `instructions:u` performance event.

The queue records the SHA-256 of each canonical configuration under distinct
domains:

```text
SHA256(UTF8("lean-eval-replay-execution-profile-v1\0") ++ canonical-json(profile))
SHA256(UTF8("lean-eval-replay-measurement-config-v1\0") ++ canonical-json(measurement))
```

Canonical JSON uses UTF-8, lexicographically sorted object keys, no whitespace,
and the v1 value subset of objects, arrays, strings, booleans, null, and
integers. The planner recomputes both digests and the locked replay task ID. It
rejects unknown fields, unsorted or duplicate work, a toolchain mismatch, and
any source/archive path that is not derived from the submission UUID.

The first task is the lexicographically smallest `replay_task_id`. A queued
task starts attempt 1. A retryable failed task starts exactly its prior attempt
plus one and uses the failure event as its next causation event.

Files under `tests/fixtures/` are contract vectors, not deployable profiles.
In particular, their VM image digest and toolchain versions are deliberately
test data. Enabling a host requires a separately reviewed profile whose image
digest is present on that host and whose digest is recorded by State.

## Public replay sequence

Run locally against reviewed files:

```bash
python scripts/replay_orchestrator.py plan \
  --queue path/to/replay-queue.json \
  --execution-profile path/to/execution-profile.json \
  --measurement-config path/to/measurement-config.json \
  --output path/to/plan.json
```

An empty queue produces `{"kind":"empty"}`. A public task produces an exact
execution request and a `replay.started` transition body. A trusted State
writer supplies the cryptographically random UUIDv7 and canonical timestamp,
appends that event, and gives its event ID to verdict conversion. This CLI
never generates State identity or time and never writes the ledger.

A private task produces a non-event `{"kind":"blocked", ...}` plan with
`private_replay_requires_d6`. It contains no transition body, does not consume
an attempt, and leaves the same task in State's replay queue. Re-running the
planner after D6 approval can therefore execute the original deterministic
task instead of encountering a false terminal state.

The VM controller must execute these phases:

1. Create a fresh VM from the profile's pinned image digest. Persistent or
   reused runners are invalid.
2. Fetch only the public source repository/commit and benchmark
   repository/commit from the request. Verify the checked-out commits exactly;
   branches, tags, default branches, and pull-request merge refs are invalid.
3. Install only the exact toolchains and component commits in the profile.
4. Remove all `.git` directories and fetch-process environment before any Lean
   elaboration. The request's `untrusted_environment` is exactly empty; a
   controller may not merge its ambient environment into it.
5. Disable network access for untrusted execution. Run the benchmark
   comparator/sandbox path with the configured memory and wall-time limits.
6. Emit exactly one schema-valid verdict on the trusted handoff channel.
7. Destroy the VM and confirm destruction. A lost runner or teardown failure
   is an orchestration failure, never a checker rejection.

`DisposableVmRunner` is the narrow host adapter: `run(request)` returns the
verdict and `destroy()` tears down the VM/registration. The helper
`run_with_disposable_vm` validates the plan before dispatch and calls
`destroy()` in a `finally` block even when execution or verdict validation
fails. Tests use an in-memory double; there is no production backend in this
foundation. The interface is deliberately provider-neutral;
its implementation must be owned and operated for Lean Eval rather than reuse
infrastructure belonging to another project.

The request describes the required network boundary but this foundation does
not implement or attest that isolation. Launch remains blocked until a reviewed
backend demonstrably disables network access before untrusted execution.

The request contains no credential, token, key, cookie, Git configuration,
cloud metadata credential, State writer, results writer, archive writer, or
release writer. Public HTTPS fetch happens before untrusted execution and must
not require authentication. A controller must fail closed if its environment
contains a write credential rather than trying to filter a large ambient
environment safely.

## Verdict and State handoff

A completed execution has a separate `checker_outcome` of `accepted`,
`rejected`, or `declined`. Checker-process `crashed` and `timed_out` execution
outcomes carry no checker verdict; all five map to distinct State events. A
failed orchestration has no checker outcome and one closed failure reason.
Statistics are present for all five reported execution outcomes and always record
checker wall time, build wall time, lines of code, file count, and separate
checker/build retired-instruction measurements.

Retired instructions are never silently omitted. Each checker/build counter contains either
`{"status":"measured","value":N}` or
`{"status":"unavailable","reason":...}`. If the measurement profile marks
the counter required, an unavailable value fails closed. Otherwise the State
verdict stores `retired_instructions: null` and the exact unavailable reason.

Convert a verdict after the started event has been durably appended:

```bash
python scripts/replay_orchestrator.py terminal-transition \
  --plan path/to/plan.json \
  --verdict path/to/verdict.json \
  --started-event-id 0198abcd-0000-7000-8000-000000000008 \
  --output path/to/terminal-transition.json
```

The output is a transition body, not a complete event. A trusted State writer
adds actor, event ID, and occurrence time, then uses the State validator and
exclusive append operation. Runner start/loss and transient fetch failures are
retryable; toolchain setup and malformed-verdict failures are not. Checker
crash and timeout have their own verdict events. No failure class is mapped to
checker rejection.

If a trusted preflight proves that an immutable public source ref, benchmark
ref, or reviewed execution profile cannot be obtained, record the registered
permanent condition explicitly rather than fabricating a runner failure:

```bash
python scripts/replay_orchestrator.py unavailable-transition \
  --queue path/to/replay-queue.json \
  --reason source_ref_permanently_unavailable \
  --evidence path/to/reviewed-evidence-locator.json \
  --output path/to/unavailable-transition.json
```

The reason set is closed to permanent conditions. The evidence locator pins a
reviewed report by repository, commit, safe relative path, and SHA-256.
Temporary policy, provisioning, approval, or capacity conditions are not
unavailability and must never append this terminal event.

## Private replay boundary and D6

Private replay is deliberately blocked, not terminally unavailable. The
planner emits no event while D6 is pending. The code exposes only a typed test
boundary receiving the byte-identical archive locator contract:

```json
{
  "schema_version": 1,
  "submission_id": "<UUIDv7>",
  "archive_repository": "<owner/repository>",
  "archive_commit": "<40 lowercase hex>",
  "archive_path": "archives/<prefix>/<submission-id>.tar.age",
  "archive_ciphertext_sha256": "<64 lowercase hex>",
  "encrypted": true
}
```

Runtime additionally correlates the path UUID and prefix to `submission_id`;
the JSON Schema pattern cannot express that equality. Test doubles can prove
the locator is passed intact, but the production CLI never invokes a private
provider. D6 must decide capability authentication, one-use enforcement, key
custody, issuance/audit records, and destruction acknowledgement before any
production execution-backend implementation or provisioning is authorized.

## Restore and incident checks

Before enabling a real runner, rehearse duplicate delivery, retry after runner
loss, counter unsupported/permission denied, invalid verdict, source or
benchmark ref disappearance, network-isolation failure, and teardown failure.
Preserve the queue source digest, plan, started/terminal event IDs, verdict, VM
image digest, and destruction acknowledgement. Never preserve plaintext
private source, keys, tokens, ambient environment, or untrusted command output
that can disclose source.
