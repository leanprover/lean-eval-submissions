# Independent-kernel runner wire contract

This version-1 contract specifies a proposed byte and process boundary between
authoritative replay and a future independent-kernel runner. It is an offline
validation contract, not an exporter or runner. The existing record-only corpus
adapter does not import or enforce these objects. Nothing here fetches source,
executes Lean, invokes a checker, writes State or Results, or qualifies a
candidate.

The tracked fixtures use synthetic identities and a two-line structural NDJSON
sample. The format-document and candidate-source pins refer to real reviewed
source blobs; every execution identity and evidence digest is synthetic and
unqualified. The fixtures are not replay evidence, an image attestation, a
checker result, or a corpus execution.

## Exact input bytes

`kernel-solution-export-input-v1` identifies the raw bytes passed to the
independent kernel. Version 1 is exactly the LF-terminated UTF-8 NDJSON emitted
for comparator's `solutionExport` by lean4export format 3.1.0. It is not source,
an archive, an olean, a workspace, or a JSON wrapper around the export.

The sidecar binds the result, replay task and positive attempt; raw byte count
and SHA-256; exporter repository, commit, binary digest, name and reported
version; the path and SHA-256 of `format_ndjson.md` at the pinned exporter
commit; Lean toolchain,
version and Git hash; the exact benchmark `config.json` commit, canonical path,
blob digest and permitted-axiom list; and the terminal verdict, State event and
historical report-entry digests. The invocation's axiom list must equal the
list parsed from those supplied configuration bytes. Repository/commit/blob
claims remain producer assertions until an approved producer verifies them.

The semantic validator checks the raw file itself: at most 64 MiB, canonical
LF termination, bounded NDJSON lines, strict JSON/UTF-8 scalars, exactly one
initial metadata record, no later metadata record, and exact agreement between
its exporter/Lean/format metadata and the sidecar. It recognizes the closed
top-level record tags and back-reference keys from format 3.1.0; it does not
reimplement lean4export's nested record semantics or prove that a malicious
producer omitted source-shaped string values.

`source_free: true` means the artifact contains the exported environment needed
by a kernel, not submission source files or repository metadata. It remains an
assertion of the reviewed producer. A future producer must capture the exact
`solutionExport` bytes that comparator actually checked, using an exclusive,
no-follow output owned by the trusted replay runtime. Regenerating similar
bytes later is not evidence that they were the bytes used by terminal replay.
No current replay verdict or historical artifact makes that assertion, so the
fixture cannot be replaced with invented production metadata.

## Fixed invocation

`kernel-nanoda-invocation-v1` fixes the first reviewed candidate source to Mathgraph
commit `3d7585c21242f29fdaa48ae9a16e16c6afe42238`. The executable, input path,
configuration path, argv and empty environment are literals. The candidate
binary digest, export-sidecar digest and raw input digest are mandatory. The
binary is explicitly `unqualified`: its digest is bound but has not been
approved by an image/build provenance registry.

The `attempt_id` is also explicitly `unbound`. Version 1 carries the asserted
corpus-attempt label through the transcript and attestation, but lacks the
series configuration and inventory identities required to recompute the
adapter's `kca1_...` identity. A future integration must add and verify those
inputs before converting this label into a corpus runner record.

The nanoda-compatible configuration is a closed object. Its exact bytes are
UTF-8 canonical JSON (sorted keys, compact separators, no trailing newline),
and `configuration_sha256` is computed over those bytes. Version 1 forces
file input, the fixed read-only export path, a sorted unique permitted-axiom
list, hard failure for unpermitted axioms, and the natural-number and string
extensions. The axiom list must match the exact benchmark configuration bytes
bound by the export sidecar. A hash without the corresponding closed
configuration cannot be executed.

The process receives exactly:

```text
/opt/lean-eval/bin/sokonanoda /run/lean-eval/nanoda-config.json
```

with an empty environment. The raw export is mounted read-only at
`/run/lean-eval/solution-export.ndjson`.

## Outcome boundary

The pinned Mathgraph producer makes only two exit statuses unambiguous:

- exit `0` is `accepted`;
- exit `2` is `declined`.

Only the kernel fault signals `SIGILL`, `SIGABRT`, `SIGBUS`, `SIGFPE`, and
`SIGSEGV` are `crashed`. Every other signal, including operator, shutdown and
resource signals, is blocked as `ambiguous_signal`, because a process signal
alone does not prove which event occurred. An independently identified enforced
memory limit is `crashed`, and an independently identified enforced wall limit
is `timed_out`; both require a closed limiter code and evidence digest.
Invocation resource limits are part of the canonical object, and transcript
measurements cannot exceed them.

A missing reviewed input is `export_unavailable`; a pre-execution failure of
the version-1 NDJSON validator is `export_format_unsupported`. Both export
outcomes have zero checker invocations and zero process statistics. The former
requires a validator code, evidence digest and null observed-input digest; the
latter requires a validator code, evidence digest and the digest of the bytes
the validator actually rejected. These are structured claims, not evidence
created by this repository.

Exit `1` is deliberately not mapped to `rejected`. The pinned `src/main.rs`
blob (whose digest is part of the invocation protocol) uses it
for checker errors, configuration/I/O/parser errors, and caught Rust panics.
Its exact source therefore cannot distinguish rejection from an internal
failure. The transcript must record `status: blocked` and
`reason: ambiguous_exit_status`; it cannot be converted into a runner record or
promotion evidence. Other unregistered exits are blocked separately. A safe
`rejected` outcome requires a reviewed producer change that emits a structured,
versioned result distinct from internal failure. This contract does not guess
at that future protocol.

## Transcript and attestation

`kernel-runner-transcript-v1` binds the exact attempt, raw input and canonical
invocation. It records one closed process termination, bounded stdout/stderr
byte counts and digests, wall time, peak memory, checker invocation count, and
the only classification implied by that termination. Stream contents do not
cross this source-free boundary; truncation is forbidden, zero-length streams
must use SHA-256 of the empty byte string, and that digest cannot label a
nonempty stream. Both the schema and semantic validator independently reject
altered or guessed classifications; a caller must apply both.

`kernel-runner-attestation-v1` binds the exact transcript and invocation to an
asserted runner commit/image and the fixed `x86_64` Ubuntu 24.04 platform. The
image is explicitly `unqualified`; the contract binds a digest but does not
assert approved build provenance. The attestation
asserts a fresh instance, disabled network, absent credentials, read-only
input, successful destruction, and a source-free handoff. These booleans are
claims by a future reviewed runtime; the schema does not prove them. The corpus
adapter may use SHA-256 of the canonical transcript and attestation only after
that runtime and its evidence path exist.

## Remaining execution blockers

This contract enables implementation review but not a real shard. Execution
still requires all of the following:

1. a trusted comparator/replay producer that captures the exact raw solution
   export used for the terminal authoritative result and records its sidecar;
2. a structured Mathgraph result protocol that separates rejection from
   internal failure;
3. a reviewed exact-image runner that enforces the fixed paths, empty
   environment, resource limits, network/credential boundary and destruction,
   and emits these transcript and attestation objects;
4. real terminal historical replay evidence and an inventory built from it.
5. the series configuration and inventory identities needed to derive and
   verify the corpus `attempt_id` instead of carrying an unbound label.

Until then, `scripts/kernel_corpus_runner_adapter.py` remains record-only, does
not consume this wire contract, and all corpus promotion remains human-blocked.
