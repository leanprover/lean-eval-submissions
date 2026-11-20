# Stock Lake measurement adapter: inactive implementation slice

This adds a stock-specific wrapper, trusted private trace and reducer. It does
not modify active replay admission, the evaluator, Worker readers or image
recipes. The only existing runtime change is an optional `started_observer`
parameter on `run_measured`; its default is `None`, preserving legacy callers.
No profile, request, image or replay task becomes executable through this change.

The intended caller is the original benchmark WorkspaceTest → stock `lake test`
→ bundled `lake comparator`, with the original config and bundled exporter,
NanoDa and native kernel. The wrapper uses the supported `COMPARATOR_BWRAP` hook.
It does not patch Lean/Lake or replace bubblewrap, commands, mount arguments,
transport, limits, or toolchain binaries.

## Trusted policy and lifetime

A future reviewed supervisor must derive the immutable policy from a qualified
image and the original selected benchmark configuration. The private policy
contains exact tool paths/hashes, real-bubblewrap hash, original modules and
declarations, expected environment strings, workspace/toolchain/Lake/temp paths,
and a fresh binding: task, attempt, profile/config digests, random generation,
problem/revision and monotonic generation start. This library does not establish
historical authority, classify a Result, qualify an image, or derive that policy.

`initialize` refuses existing journal/lock/clock files. Their directory must be
owned by the supervisor, mode0700, outside the Source-writable workspace, with
no symlink resolution. Files are single-link regular files, mode0600, opened
with no-follow/CLOEXEC. The policy/journal header binds the complete policy hash
and exact generation. A private lock serializes short journal updates; lock
acquisition is nonblocking. Journal writes are atomic and bounded to64KiB.

Before integration, qualify that these control paths are protected by the real
stock sandbox and the immutable outer runtime. In particular, resolution retains
the stock `--share-net` argument; outer network denial must remain in force.
Source may never choose policy, journal, real-bwrap executable, or generation.
The production entry point uses fixed absolute paths under `/opt/lean-eval`,
`/run/lean-eval` and `/usr/bin/bwrap`, and ignores environment/argv overrides.

The Python wrapper binds its own death to its original parent using Linux
`PR_SET_PDEATHSIG`, with the parent-race check. Native bwrap retains the original
`--die-with-parent` argument. This matters because an extra measurement parent
must not keep a Sandbox alive after the original Lake owner dies. No new process
group/session is created by the production wrapper. Child signals are recorded
as signals and propagated as signals after the completed observation.

## Exact invocation and phase evidence

The option parser consumes option arities before recognizing the real `--`
command separator. A value equal to `--` is a value, not a delimiter. Every
recognized invocation must match the pinned stock sandbox template, exact
command, original binds/environment/network policy and tool hash. Module and
declaration strings remain literal Unicode argv elements; no shell is involved.

The sequence is exactly:

| Boundary | Measurement bucket |
| --- | --- |
| resolve | excluded |
| Challenge build | excluded |
| Challenge export | excluded |
| Solution build | build |
| Solution export | build |
| bundled NanoDa | checker |
| bundled native Lean checker | checker |

Native Lean also creates a temporary config even though its command consumes
only the export. Both kernel configs must have the exact stock closed fields.
Their private regular files and the shared export must be under the bound host
temporary directory, outside Source writes. The export's private inode/path
commitment, size and modification time must match across kernels. No file
contents, raw argv, paths, proof output or exception text enters the trace.
Kernel inputs are bound read-only by original stock arguments.

The wrapper delegates `[real_bwrap, *original_argv]` unchanged, with original
cwd/stdio and inherited descriptors. Its own journal/lock/clock/perf descriptors
are noninheritable. Unknown invocations or observation failure invalidate
admission but still delegate the one original bwrap operation where possible.
There is **no fallback re-execution** after a child might already have started.
A post-launch observer failure leaves invalid/incomplete evidence and exit125;
it never retries the mathematical command.

Only the four included calls open counters. Completed values come directly from
the existing inherited userspace `instructions:u` primitive and its monotonic
wall timer. Journal validation/fsync/hash work is outside that measured interval.
Unavailable counters retain their closed reason; they are never manufactured0.
Counters sum only when every contributor is measured.

## Measurement-start and timeout handshake

A journal `active` record is written before the operation; its intent timestamp
does **not** prove that measurement or child execution started. The optional
primitive hook publishes its exact measurement-start `monotonic_ns` into a
fixed112-byte clock slot: policy hash, generation, stage index, start timestamp
and checksum. It uses a no-follow/CLOEXEC private FD and nonblocking flock,
one bounded `pwrite`, and no fsync. Callback failure never suppresses or repeats
the sandbox call. Completed included records require a successful start receipt.

The hook runs after the primitive's timer/counter start. Its small publication
overhead therefore belongs to the measured interval; it is not represented as
free. This is the measurement boundary, **not proof that Popen succeeded**.
Launch errors invalidate the trace. A partial/corrupt/unbound/missing slot does
not establish a measurement boundary. No persistence across host loss is claimed.
Qualify the fixed control location as local memory-backed storage before runtime
activation; a network filesystem is not an acceptable clock channel.

After actually stopping the bound generation, the supervisor reads the private
clock slot and supplies its own exact binding/cutoff-nanoseconds/stopped record.
An included active phase is charged only cutoff minus that real measurement
start, not intent time or a legacy count-based allocation. Interrupted counters
are honestly unavailable. An excluded active phase charges neither bucket.
Missing start/stop ownership, impossible timing, source-writable clocks or a
different generation produce existing `failed/verdict_invalid` evidence.

This slice supplies the writer, bound reader and reducer. A future evaluator
integration must provide the actual per-problem driver record, one-generation
Supervisor stop/cutoff, and provider cleanup proof. A counter slot alone does not
establish provider destruction or successful mathematical execution.

## Verdict semantics and remaining integration

Acceptance requires all seven boundaries,2 build plus2 checker completions,
all0, the trusted **per-problem `lake test`** exit0, and the exact passed problem
and statement revision. The enclosing evaluator Python exit status is not that
driver record. Either normal nonzero kernel produces decline, including NanoDa
failure followed by native success. Signals and actual timeouts remain execution
outcomes. Contradictory, stale, missing or impossible evidence is invalid.

Stock comparator's generic exit1 before kernels conflates comparison, parsing
and I/O errors. A successful export prefix plus driver1 therefore remains
`failed/verdict_invalid`; it is not a demonstrated proof rejection. No output
message parsing or upstream compiler patch is used to invent a distinction.

The tests use explicit synthetic tools/records for contract cases and real
bubblewrap for namespace/network/control-file protection, parent death and a
stopped in-flight measurement. That timeout test injects200ms of journal delay
and proves it is excluded from the active measurement total. Separate tests
exercise real inherited FDs, private/perf FD closure, signal propagation,
post-launch failure, false separators, two-kernel failures and stale clocks.
These are not stock-image/provider qualification or a new admitted replay.

Remaining work is the reviewed profile/config/request and selected-workspace
integration, authoritative evaluator/supervisor wiring, real stock original-driver
image qualification, Worker parity, actual provider namespace/limits/destruction,
and per-Result classification/archive/State authority. Existing legacy profiles,
digests and active readers must remain unchanged until those gates are proved.
