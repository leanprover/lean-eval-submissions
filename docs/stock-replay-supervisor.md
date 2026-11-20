# Inactive original stock driver supervisor

This source slice depends on PR2218 (configuration/request/profile contracts)
and PR2220 (the stock measurement wrapper and reducer). It adds no image recipe,
Worker/evaluator import, admission reader, workflow, permission, Source policy,
runtime activation, or historical replay authority. The inactive image entries
must be installed at their fixed paths only by a separately reviewed image.
An eventual upstream-main PR must retain both prerequisites explicitly.

The trusted child command is the absolute pinned stock `lake test`, in the
selected workspace. Its unchanged original `WorkspaceTest.lean` invokes the
bundled comparator and enforced bundled NanoDa; the comparator invokes the
measurement entry through its supported `COMPARATOR_BWRAP` hook. The entry
delegates the original seven bubblewrap argument vectors unchanged. There is
no Lean/Lake patch, substitute comparator, disabled inner sandbox, extra inner
argument, retry, or manufactured driver status. This finite slice supports the
original `Challenge`/`Solution` workspace module layout only.

## Private preparation boundary

`context_from_request` validates request3 against trusted original benchmark and
authority bytes using PR2218, and binds its exact task, attempt, P, M, problem,
revision, benchmark commit/tree/toolchain and configuration digest. It checks
the four measurement tools, Lean, and bubblewrap hashes against the profile.
The request SHA-256 and these bindings accompany the resulting private evidence.
It performs no Git/network/Source operation and creates no historical authority.

The caller must independently establish the original Git origin, commit/tree,
file modes, selected configuration, generator source/command and faithful derived
workspace before calling this factory. Passing caller-supplied bytes does not
establish those facts. This slice does not implement that preparer, qualify an
image/provider, verify runtime CPU/counters/memory/network resources, or admit
request3 into the active controller. A private context supplied directly to the
library is a trusted preparer input, not a public contract/admission interface.

The derived-output commitment is SHA-256 of the canonical prepared manifest
(schema2) excluding `source_sha256`. Its `files` object hashes ten required trusted
files: `Challenge.lean`, `Solution.lean`, `WorkspaceTest.lean`, `config.json`,
`lakefile.toml`, `lake-manifest.json`, `lean-toolchain`, and the preprimed
`.lake/build/bin/workspace_test`, `README.md` and `holes.json`. The genuine
optional `ChallengeDeps.lean` is included and rebound read-only when present;
its absence/presence is committed too. These names come from the bounded
original generated filesets, not submitter-provided configuration. The original
README/holes bytes are retained unchanged. Each value binds file SHA-256 and
permission mode. The `prepared_tree_sha256` additionally commits a sorted, streaming closure
of every non-Source directory/file, including all preprimed caches, traces and
package dependency state. This avoids an unbounded JSON file inventory while
checking the entire prepared tree before go. The original helper's direct
`.lake/packages` alias and its nested package
aliases are supported, including Lean Pool's individual regular-file aliases
and private `.lake/package-overrides.json`. Their canonical outside-project
target and complete bytes/tree are committed. Aliases into/around the project,
temporary or control directories, Source aliases and cycles are refused.
Trusted closure files retain the finite 256 MiB/single-link bounds; this slice
makes no claim about a larger/unqualified dependency layout. The qualified
image/preparer must establish supported immutable dependency bytes first.
Source symlinks, unknown root entries and an alternative root `lakefile.lean` are refused.

**Source is exactly `Submission.lean` and `Submission/**/*.lean`. Solution.lean
is trusted generated code.** Source bytes/names have a separate `source_sha256`
commitment in the private runtime context and cannot be swapped after preparation;
they are excluded from the benchmark-derived closure digest. Both trusted
original generation and the overlaid Source's request/commit provenance must be
proved by the preparer. Hashing a preseeded arbitrary cache does not prove its
pristine origin. No Source is compiled by the context factory.

An exclusive marker in the workspace binds its first fresh generation and makes
that directory single-use even with another control directory. Source cannot
remove it through the original read-only project mount. The agent permits only
that exact bound marker while rechecking the prepared closure before Popen.
New attempts require a fresh copy of the independently authenticated pristine/
preprimed base; the supervisor never blesses or resets a Source-modified cache.

The private context commits the complete policy template excluding only fresh
generation/start. It also commits the expected Lean launcher, instrumentation
files and interpreter launcher bytes. The factory checks Lean and measurement
launcher hashes against the profile; runtime rechecks them and the immutable installed instrumentation before
go/driver launch. Declarations, paths or self-consistent replacement hashes
of those launcher files cannot be paired with an unchanged context. These file hashes do not
cover dynamically loaded Lean/Lake libraries, standard-library/prelude oleans,
Python stdlib, the ELF loader or bubblewrap's shared libraries. The entire
runtime closure must separately be pinned and qualified as immutable image
content; this context is not that qualification. The output includes actual
policy,
policy-template and instrumentation commitments. Production uses only the fixed
five instrumentation targets; explicit test overrides are context-bound and
permit only the additional conventional glibc-loader target.

The private context records the invoking `lake test` PATH separately from the
measurement policy's comparator-inherited/resolved PATH and LEAN_PATH. Original
Lake prepends its own paths; equating these values changes the invocation.
Driver PATH directories must be canonical, non-writable by group/other, outside
the project and temporary directory. The private policy/context must come from
the actual original driver environment and pinned stock toolchain; a guessed
PATH is not evidence. Exact inner invocation matching remains mandatory.

PR2220's reducer has a 19,800,000 ms maximum generation cutoff. Unsupported M
limits are refused before launch, never rewritten. This slice explicitly reserves
11,001ms for both bounded teardown waits, millisecond rounding and scheduling
margin, so admission requires M+reserve<=19,800,000ms. M explicitly begins at
generation_started_ns, before private control writes,
agent validation and driver Popen; those startup operations consume its declared
interval. Refusal does not rewrite that origin or an admitted M, and the reserve
does not guarantee scheduling latency: an actual cutoff outside the reducer
bound remains visible (`cutoff_representable=false`) and timeout evidence invalid. PR2218's 21,600,000 ms
synthetic fixture is therefore not executable by this slice. A future packet
must select an authorized compatible M with teardown reserve or separately
review the measurement contract; a cutoff beyond the reducer bound remains
invalid. This supervisor does not establish aggregate memory enforcement or
runtime qualification from M's presence in the context.

## Kernel ownership and stop proof

An extra outer instance of the existing pinned bubblewrap uses
`--unshare-all --die-with-parent --new-session`. Its outer network namespace
remains isolated even when the original inner resolve invocation retains
`--share-net`. Root/toolchain/instrumentation are read-only; the workspace and
trusted temporary directory are the explicit writable mounts. Trusted workspace
files are rebound read-only. `/run/lean-eval` is a private0700 control mount,
outside both writable trees; `/run/user` exists for the untouched inner stock
mount grammar. No cgroup delegation, host privilege, or new backend is assumed.

Before any Source-capable command, the trusted inner agent proves the fresh
generation over private FIFO descriptors and waits for one go byte. The parent
pins its unreaped direct bwrap child with a pidfd, reads the real bwrap info FD,
opens the reported PID namespace, and verifies namespace inode, namespace PID1,
parent relationship, and start ticks. It pins that init with a pidfd and repeats
identity/liveness checks before go. The namespace must differ from the owner's.
A forged/reused/unrelated reported PID is never a signal target. Before verified
ownership, only the pinned owned outer process can be stopped and no stopped
generation proof is emitted.

After verification the parent writes the fresh policy, context, measurement
journal/clock and bound generation-owner record, then releases the agent once.
The agent marks private control descriptors CLOEXEC and closes the go FD. Its
actual Popen/wait of `lake test` supplies a bound per-problem started/termination
record. Exit0 yields exactly the locked problem/revision, following the existing
per-problem score rule; the enclosing Python/bwrap exit never supplies acceptance.
There is no synthesized `results.json`, start receipt, exit, or generic rejection.

On completion, timeout, protocol error or handled termination signal, the owner
signals the verified init through its pidfd, waits for actual init death and
outer reap, and only then records monotonic cutoff and the bound stop receipt.
Linux namespace-init death kills every process in that PID namespace, including
new-session descendants which process-group signaling cannot contain. A later
termination signal cannot interrupt this teardown. SIGKILL/owner death uses
the existing bwrap parent-death guard; a killed owner cannot fabricate a final
receipt. Failure to establish stop leaves invalid evidence.

The control directory must be empty except for the fixed trusted input. Bwrap
information is accumulated through EOF with a finite deadline/byte limit; a
partial JSON prefix cannot be mistaken for the whole record. Init identity can
be bound only once. Control files are exclusive, bounded, no-follow/CLOEXEC regular files, and cannot
be reused for another generation. Frames are closed/bounded to4096bytes. The
original inner sandbox exposes no writable journal/control location or owner
FIFO descriptor. After verified stop, the reducer reads the private trace and,
for an interrupted included phase, its real measurement-start clock slot.
Cutoff-minus-start is charged; incomplete counters remain unavailable. Buffered
trusted frames may be read after owner descheduling, but actual `finished_ns`
must be no later than the exact locked generation deadline to accept. Timely
completion whose trusted finish frame was already written/buffered
remains eligible when the owner reads late. A physical exit/timestamp not yet
written before agent teardown is not recoverable proof and stays unproved. A
genuine over-M finish retains its
actual driver/finish evidence and is timed out, with all actual measured time
and stop overshoot preserved. No fake exit, capped clock or capped statistics is
substituted. Missing driver-start proof is an infrastructure failure, not a
manufactured execution timeout. Required counters keep strict semantics: an
invalid or interrupted/unavailable counter result remains verdict_invalid with
a bounded envelope, rather than relaxing required=True or raising on null stats.
An early generic driver failure remains `failed/verdict_invalid`. Rejection
requires complete seven-phase evidence with the actual failed bundled kernel.

The bounded final envelope includes the full existing verdict and separate
driver/start/stop records. Raw output, Source contents, commands and exception
messages are discarded. Private runtime paths never enter the envelope.
`provider_destruction` and `runtime_qualification` are explicitly
`not_established`: namespace stop is not provider Sandbox destruction, job
removal, scratch cleanup, persistence, image qualification or replay authority.
Those owner proofs and cleanup obligations remain mandatory before any runtime
integration. Entry success alone never admits a verdict or qualifies a row.

## Evidence and limits

Tests exercise real namespace init teardown with a new-session descendant,
another live namespace's forged init, termination/parent death, control/FD
isolation, real per-problem statuses, dual-kernel failure, and a timeout inside
an actual measured phase. They also validate complete contract bindings and
refuse changed driver bytes, identities and reused/aliased control paths.

The optional local original-driver test requires an existing offline packaged
stock4.35.0 cache. It compiles the exact original driver, runs original `lake
test`, and requires all seven actual phases, both kernels, driver exit0 and real
stop. Local Nix supplies a conventional glibc loader mount and adjusts only the
instrumentation Python interpreter/fixed bwrap installation path. Original
driver/tool bytes and all inner argument vectors remain unchanged. This is
component evidence, not the preserved provider/image/runtime qualification.

No new public operation is enabled by these tests or source files. Image
installation, selected-workspace provenance, original environment derivation,
resources/counters, provider stop/destruction and job/scratch cleanup, runtime
admission and bounded qualification require their own reviewed integration.
