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

The derived-output commitment used here is SHA-256 of canonical JSON
`{"schema_version":1,"files":{...}}`. Its exact keys are `Challenge.lean`,
`WorkspaceTest.lean`, `config.json`, `lakefile.toml`, `lake-manifest.json`,
`lean-toolchain`, and the preprimed `.lake/build/bin/workspace_test`. Each value
contains the regular file's `sha256` and numeric permission `mode`. The original
driver source must also match the configuration's original Git blob binding.
The trusted prepriming build occurs before this manifest is frozen. Solution
Source remains outside this trusted-file commitment and must retain its
separately proven request/Source provenance in the preparer. No Source is compiled
by the context factory.

The private context records the invoking `lake test` PATH separately from the
measurement policy's comparator-inherited/resolved PATH and LEAN_PATH. Original
Lake prepends its own paths; equating these values changes the invocation.
Driver PATH directories must be canonical, non-writable by group/other, outside
the project and temporary directory. The private policy/context must come from
the actual original driver environment and pinned stock toolchain; a guessed
PATH is not evidence. Exact inner invocation matching remains mandatory.

PR2220's reducer has a 19,800,000 ms maximum generation cutoff. Unsupported M
limits are refused before launch, never rewritten. PR2218's 21,600,000 ms
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

Control files are exclusive, bounded, no-follow/CLOEXEC regular files, and cannot
be reused for another generation. Frames are closed/bounded to4096bytes. The
original inner sandbox exposes no writable journal/control location or owner
FIFO descriptor. After verified stop, the reducer reads the private trace and,
for an interrupted included phase, its real measurement-start clock slot.
Cutoff-minus-start is charged; incomplete counters remain unavailable.
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
