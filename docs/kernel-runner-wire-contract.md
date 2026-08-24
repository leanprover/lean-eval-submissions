# Independent-kernel runner wire contract

This version-1 contract specifies the byte and process boundary between
authoritative replay and a future independent-kernel runner. Its validators and
wire-to-record adapter are offline and do not execute Lean or a checker. The
authoritative and historical-public replay image sources now capture the exact
solution-export bytes at the comparator boundary. The authoritative PR image
workflow may produce source-only build evidence, but that is not execution
qualification; the corresponding historical image remains unbuilt. Neither
changed image is qualified, deployed, selected, or connected to a durable
source-free artifact handoff.
Nothing here fetches source, writes State or Results, or qualifies a candidate.

The tracked fixtures use synthetic identities and a two-line structural NDJSON
sample. The format-document and candidate-source pins refer to real reviewed
source blobs; every execution identity and evidence digest is synthetic and
unqualified. Corpus-report and standalone wire fixtures are contract-local
examples rather than one executable cross-file chain; the focused adapter tests
synthesize and validate the complete cross-bound chain. The fixtures are not
replay evidence, an image attestation, a checker result, or a corpus execution.

## Exact input bytes

`kernel-solution-export-input-v1` identifies the raw bytes passed to the
independent kernel. Version 1 is exactly the LF-terminated UTF-8 NDJSON emitted
for comparator's `solutionExport` by lean4export format 3.1.0. It is not source,
an archive, an olean, a workspace, or a JSON wrapper around the export.

The sidecar binds the result, replay task and positive attempt; raw byte count
and SHA-256; exporter repository, commit, binary digest, name and reported
version; the path and SHA-256 of `format_ndjson.md` at the pinned exporter
commit; comparison-framework repository, commit, and protocol; Lean toolchain,
version and Git hash; the exact benchmark `config.json` commit, canonical path,
blob digest and permitted-axiom list; and the terminal verdict, State event and
historical report-entry digests. The comparison framework must equal the exact
checker series. The series also contains a sorted, unique producer profile for
each benchmark commit, and the offline adapter requires the sidecar's complete
exporter, format, and Lean identity to equal the profile selected by that exact
attempt. A historical series intentionally spans five exporter commits and
several Lean toolchains; one series-wide exporter or Lean identity would reject
valid historical rows. Repository/commit/blob claims remain producer assertions
until an approved producer verifies them and the reviewed series freezes the
resulting profile.

The semantic validator checks the raw file itself: at most 64 MiB, canonical
LF termination, bounded NDJSON lines, strict JSON/UTF-8 scalars, exactly one
initial metadata record, no later metadata record, and exact agreement between
its exporter/Lean/format metadata and the sidecar. It recognizes the closed
top-level record tags and back-reference keys from format 3.1.0; it does not
reimplement lean4export's nested record semantics or prove that a malicious
producer omitted source-shaped string values.

`source_free: true` means the artifact contains the exported environment needed
by a kernel, not submission source files or repository metadata. It remains an
assertion of the reviewed producer. The prepared comparator patch now labels
only the solution export as `solution-export`; the trusted measurement adapter
tees that exact bounded LF-terminated stdout both to comparator and to the
exclusive, no-follow, mode-0600
`/run/lean-eval/solution-export.ndjson`. Failed, empty, over-limit, or duplicate
captures are removed or refused. Both authoritative and historical-public
commands remove a stale capture before evaluation and scrub any capture in
their `finally` cleanup. A process-level `SIGKILL` can bypass that cleanup, so
the future handoff must use attempt-bound completion evidence and must never
accept a fixed-path incumbent as current-run output; Sandbox destruction is the
final cleanup boundary today. This is the producer capture seam, but no
reviewed handoff retrieves it or finalizes the sidecar against terminal
State/report evidence. Regenerating similar bytes later is not evidence that
they were the bytes used by terminal replay. No current replay verdict or
historical artifact makes that assertion, so the fixture cannot be replaced
with invented production metadata. Because the trusted tee sits inside the
measured solution-export invocation, current build-phase wall time and retired
instruction counts include its forwarding/capture overhead; image qualification
must review and freeze that changed measurement profile.

Capture policy remains advisory, but forwarding is part of comparator execution.
If the tee cannot forward the child's stdout, `replay-measure` records exit 125
for that build-phase invocation before returning failure. The authoritative
verdict therefore treats the infrastructure failure as a crash rather than a
submission rejection. If the metrics store itself cannot be read or atomically
updated, the adapter discards the complete store before returning 125; stale
phase history therefore cannot turn a persistence fault into a verdict. The
historical-image local verification mount reserves 80 MiB for `/run/lean-eval`,
covering the 64 MiB capture ceiling plus metrics and atomic-update headroom.

## Fixed invocation

`kernel-nanoda-invocation-v1` fixes the first reviewed candidate source to Mathgraph
commit `3d7585c21242f29fdaa48ae9a16e16c6afe42238`. The executable, input path,
configuration path, argv and empty environment are literals. The candidate
binary digest, export-sidecar digest and raw input digest are mandatory. The
binary is explicitly `unqualified`: its digest is bound but has not been
approved by an image/build provenance registry.

The invocation's `attempt_id` remains explicitly `unbound`: the standalone
wire object does not contain the series and inventory. The offline integration
adapter supplies those exact objects and their deterministic shard plan,
recomputes the `kca1_...` identity, selects the one producer profile registered
for the attempt's benchmark commit, and requires the invocation, transcript,
attestation, export sidecar, terminal evidence, candidate, producer profile,
runner, configuration, and limits all to match before emitting a runner record.

The nanoda-compatible configuration is a closed object. Its exact bytes are
UTF-8 canonical JSON (sorted keys, compact separators, no trailing newline),
and `configuration_sha256` is computed over those bytes. Version 1 forces
file input, the fixed read-only export path, a sorted unique permitted-axiom
list, hard failure for unpermitted axioms, and the natural-number and string
extensions. Historical benchmark configuration bytes retain their exact
order-preserving unique axiom list. The canonical candidate invocation sorts
that same list, so historical source order cannot change either the permitted
set or the executable configuration digest. A hash without the corresponding
closed configuration cannot be executed.

The series-wide candidate `configuration_policy_sha256` hashes the five fixed
policy fields (file input, fixed export path, hard axiom failure, natural-number
extension, and string extension) and deliberately excludes `permitted_axioms`.
The latter is problem-specific: its exact list is bound per attempt by the
benchmark configuration, full invocation `configuration_sha256`, transcript,
and attestation. A corpus may therefore contain problems with different
reviewed axiom lists without weakening the common candidate policy.

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
at that future protocol. The generic runner-record schema and semantic adapter
also exclude `rejected`, so a hand-authored bundle cannot bypass this wire
boundary. Historical inventory rows may retain an authoritative `rejected`
outcome; only the unqualified candidate's new outcome is restricted.

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

## Offline wire-to-record integration

For every planned `run` attempt, place the exact raw input in `inputs/` as
`<attempt_id>.input` and place these five files in `wire/`:

```text
<attempt_id>.export-metadata.json
<attempt_id>.benchmark-config.input
<attempt_id>.invocation.json
<attempt_id>.transcript.json
<attempt_id>.attestation.json
```

Then build the existing version-1 runner-record bundle:

```bash
python scripts/kernel_wire_record_adapter.py \
  --series series.json \
  --inventory inventory.json \
  --plan plans/shard-0000.json \
  --inputs-dir inputs/shard-0000 \
  --wire-dir wire/shard-0000 \
  --output runner-records/shard-0000.json
```

Both directories must be real, exact-membership directories containing only
regular, non-symlink, bounded files. The adapter checks each open file's device,
inode, mode, link count, size, modification time, and change time before and
after its read, then compares the same identities for the complete file set
before and after validation. It also detects directory membership metadata
changes and applies aggregate byte limits; callers must keep the artifact
directories immutable. Every JSON object passes its tracked Draft
2020-12 schema and semantic validator. The adapter then binds the complete chain to the exact
series, inventory, and deterministic plan; it emits only classifications
implied by the process termination. A blocked exit or signal cannot become a
runner record. Because this adapter already possesses and validates the exact
input bytes, it also refuses `export_unavailable` and requires an
`export_format_unsupported` transcript to identify those bytes by digest.
Output uses the existing exclusive atomic writer and is passed
through the existing runner-record validator before publication.

The adapter imports no process, network, credential, repository-write, State,
Results, queue, or promotion interface. It validates claims emitted by a future
qualified runner; it does not make an unqualified attestation true.

## Remaining execution blockers

This integration enables implementation review but not a real shard. Execution
still requires all of the following:

1. build and qualification of both affected authoritative and
   historical-public replay images, including an accepted historical probe
   that actually executes Lean, comparator, and `replay-measure` (the existing
   runtime-boundary build/unwrap/egress/destruction probe is insufficient), plus
   a reviewed source-free handoff that retrieves the captured bytes before
   Sandbox destruction and finalizes the sidecar against exact terminal
   verdict, State-event, and report-entry evidence;
2. the proposed structured Mathgraph result protocol must land upstream, then
   its exact accepted-result staging probe in
   [`kernel-structured-accepted-probe.md`](kernel-structured-accepted-probe.md)
   must pass before the corpus contract adopts separate rejection and internal
   failure outcomes;
3. a reviewed exact-image runner that enforces the fixed paths, empty
   environment, resource limits, network/credential boundary and destruction,
   and emits these transcript and attestation objects;
4. real terminal historical replay evidence and an inventory built from it;
   no State append may rely on this lane before the actual-execution image
   qualification and reviewed evidence handoff above exist.

Until then, both adapters remain record-only and all corpus promotion remains
human-blocked.
