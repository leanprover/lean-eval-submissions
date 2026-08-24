# Replay orchestrator and disposable-VM interface

The Wave 2 foundation plans public-source replay and checks the result returned
by a disposable runner. A separate manual `public-replay-smoke.yml` workflow
replays one reviewed historical public result without credentials. The smoke
does not invoke the authoritative queue, append State, write results, decrypt
an archive, mint a capability, or publish a release. It is reproducibility
evidence, not the production replay backend or ranking evidence.

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
and the schema-version-1 value subset of objects, arrays, strings, booleans,
null, and integers. The planner recomputes both digests and the locked replay task ID. It
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

### Credential-free historical smoke

The tracked `two_plus_two_issue_906` fixture binds the original public source,
benchmark commit and toolchain, evaluator commit, successful workflow run, and
declared result. The manual workflow runs only through the protected
`replay-staging` environment and has repository contents read permission. It:

1. anonymously verifies the source repository is still public;
2. restores source, benchmark, and evaluator by exact 40-character commits;
3. installs the exact recorded Lean and reviewed Go/Rust/checker pins;
4. removes workflow, source, evaluator, benchmark, and component-checkout Git
   metadata before the sandbox probes and untrusted Lean; the anonymous
   Mathlib package cache retains only its public dependency metadata;
5. replays only `two_plus_two` through the original evaluator's
   `evaluate_submission.py` and the benchmark's comparator/landrun boundary;
6. uploads only results, summary, and strict source-free smoke evidence.

The artifact records observed GitHub-hosted runner image, CPU, architecture,
kernel, total wall time, source LOC/file count, and retired instructions when
the host permits `instructions:u`. GitHub-hosted image/CPU observations are not
a pre-pinned production profile, and the aggregate timing is not split into
build and checker measurements. Therefore this smoke proves proof replay and
security-boundary wiring only. It cannot produce a `replay.started` or terminal
State event. The authoritative path below remains gated on a reviewed,
pre-pinned execution profile and backend.

The first hosted smoke completed in run `32499490261` at workflow commit
`757b0831018dd6ad88092eff8a2f4b3245a456d6`. Its source-free artifact records
an accepted `two_plus_two` revision 1 replay on Ubuntu 24.04 image
`20260816.277.1`; the host did not report the optional retired-instruction
counter. This evidence completes only the credential-free historical smoke,
not isolated private replay.

### Independent-kernel compatibility smoke

`kernel-shadow-smoke.yml` is a separate manual, credential-free preflight for
candidate checkers. It does not alter the required checker set, submission
acceptance, Results, State, replay queues, or releases. The first v1 fixture
uses the already-accepted public `two_plus_two` solution and pins:

- LeanEval `21c6c02178e14cccc54b6e90e4836d1ca0e9c7e6`, Lean 4.33.0, and Mathlib
  `6f1ef4e5dd604a435bddba4747b13970cd65d2a1`;
- lean4export `15f6055e299ad5b89345e533cc2192f4cc00f659`;
- comparator `19e111e2141cf333c7daff0f64c5f24acc91dd2e`, whose multi-kernel interface
  is used only for this post-acceptance shadow invocation; and
- MathGraph `3d7585c21242f29fdaa48ae9a16e16c6afe42238`, selected because its pinned
  Arena declaration had 121/121 expected acceptances, 66/66 expected
  rejections, and zero declines. The experimental checker is not trusted merely
  because the Arena result is clean.

The workflow independently restores those commits, confirms that the source is
still anonymously public, verifies the Arena declaration, builds every
component from source, strips checkout credentials and nondependency Git
metadata, runs the standard sandbox/environment probes, overlays only
`Submission.lean` and `Submission/**/*.lean`, and invokes the candidate through
comparator's sandboxed external-kernel protocol. Only strict JSON evidence is
uploaded. The candidate is labeled `mathgraph-noda` internally so comparator
uses the candidate's documented nanoda-compatible configuration-file protocol;
the public checker identity remains `mathgraph`.

This smoke proves one real LeanEval/exporter compatibility point. It is not a
corpus backtest, a performance measurement, or promotion evidence by itself.
Promotion still requires no incorrect Arena verdicts, full current-corpus
support, agreement with adjudicated historical results, distinct recording of
reject/decline/crash/timeout, and acceptable profile-pinned runtime. The
workflow is fixed to public source and must not be generalized to private
archives; private replay uses the distinct encrypted-archive controller path.

The downstream source-free checker-series and full-corpus reporting contract is
documented in [`kernel-corpus-report.md`](kernel-corpus-report.md). Its
preparation and aggregation foundations do not turn this one-result smoke into
authoritative corpus evidence, and actual corpus execution remains gated on the
reviewed historical replay inventory and credentialed replay lane.

### Authoritative queue path

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

A private task produces the same execution-plan envelope, but its source is an
encrypted archive locator pinned by repository, commit, canonical path, and
ciphertext digest. It contains no source Git repository or source commit. The
controller must fetch exactly that ciphertext, obtain a submission-bound
single-use unwrap, and pass only the plaintext per-archive identity into the
fresh executor. Planning private work is now supported; production queue
consumption remains disabled until the reviewed controller and State-writer
handoff are deployed and their staging evidence is recorded.

The VM controller must execute these phases:

1. Create a fresh VM from the profile's pinned image digest. Persistent or
   reused runners are invalid.
2. Fetch only the pinned public source repository/commit or the exact private
   archive locator, plus the benchmark repository/commit. Verify every commit
   and digest exactly; branches, tags, default branches, pull-request merge
   refs, and archive-directory enumeration are invalid.
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

## Pre-enable evidence

The single staging acceptance test must exercise one-use refusal, wrong-archive
refusal, network isolation, and confirmed teardown. Preserve the queue source
digest, plan, started/terminal event IDs, verdict, VM image digest, and
destruction acknowledgement. Never preserve plaintext private source, keys,
tokens, ambient environment, or untrusted command output that can disclose
source. No recurring recovery drill or separate alarm subsystem is required.
