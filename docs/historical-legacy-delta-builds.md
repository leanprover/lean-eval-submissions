# Legacy final-delta workspace preparation

`scripts/prepare_historical_legacy_delta_builds.py` prepares exact original
legacy-generation inputs, then materializes selected workspaces by invoking the
original benchmark's built `lean-eval generate --problem ID` executable. It is
inactive: no controller, evaluator, image recipe, workflow, classifier or task
reader imports it. The existing frozen public/private matrix generators and
their 439-archive baseline remain unchanged.

The current readiness snapshot accounts for all 32 rows / 664 Results: 27 legacy
rows / 652 Results receive build preparations; five stock-Lake rows / 12 Results
remain explicit pending entries for a separate stock generator contract. This
is not a final cutoff or an assertion that every Result is executable.

## Input and original-generation binding

`matrix` takes a readiness JSON path, its expected raw SHA-256 and a local
benchmark Git repository. It verifies content-addressed row files, actual
commit/tree/blob identities, harness family, original root toolchain and
configuration, original generator sources, per-problem manifest/module/holes,
statement revisions and committed pristine workspaces. Legacy rows cannot
silently select root configuration for a multi-config benchmark. Stock entries
require the actual stock harness hash; relabeling a legacy row cannot omit it.

The reusable `build_matrix` layer accepts exact rows and provenance so a future
reviewed final classifier can supply final inputs without modifying baseline
counts. It produces build evidence only. The current command-line input adapter
intentionally accepts only the existing nonfrozen readiness snapshot contract;
an exact final input adapter still needs the reviewed final classification
contract. Neither layer creates image, source, capacity, migration, task or
activation authority.

`export` consumes a byte-pinned preparation matrix and creates an exclusive,
owner-only session containing a detached exact original Git checkout. It
recomputes the original row from Git rather than trusting a resealed JSON object.
No generator is executed by export.

`generate` requires a build observation binding the original commit/tree,
generator source digest, original built executable hash and exact stock Lean
and Lake executable hashes. The future trusted image builder must actually
build that CLI from the original checkout and pinned dependencies. A caller's
hash receipt is **not** proof of that build, historical authority, image
qualification, or a substitute for it. There is no current-generator fallback,
Python alternate generator, patched Lean/Lake or alternate toolchain route.

Generation verifies clean original tracked inputs, uses a clean environment
and the exact observed toolchain prefix, and invokes the original CLI once for
each selected problem. It removes that problem's committed generated directory
first, so an unchanged old workspace or a successful no-op cannot establish
fresh generation. The owned session is claimed before the first invocation;
failure leaves no success proof and requires a fresh session for another try.
Generator changes outside `generated/`, symlinks, executable outputs and build
cache trees are rejected. The existing evaluator's root-manifest/toolchain/
require checks are reused without alteration. Original Git pins and derived
workspace file hashes are recorded separately.

The legacy component profiles retain reviewed comparator, landrun and NanoDa
pins and cold measurement semantics. Exact 4.34.1 explicitly uses unmodified
exporter source `076e8e57707e813375e8f9da8bf989799ace9680` built with stock
4.34.1; it does not invent an upstream 4.34.1 exporter tag. Compatibility
evidence does not qualify an image.

## Remaining execution gate

All 23 benchmark Git objects are available, and all current readiness rows can
be checked against them. No historically built original CLI has been supplied
or approved for these new preparations. Tests exercise real local Git exports
and explicitly synthetic CLI executables; they do not prove those historical
Lean builds or regenerate all 23 benchmarks. Actual original dependency/CLI
builds, component/checker checks, immutable images and runtime qualification
remain necessary. Existing Docker recipes build the original CLI and preprime
the **committed** generated workspaces; they do not call this new materializer.
Their old retained-workspace evidence must not be described as fresh original
generation evidence.

Generated JSON remains `unqualified` with explicit `authority_status: none...`.
It is not consumable as a replay image/profile lock. Final cutoff,
classification, original source routing, archive capacity, migration,
admission and activation remain separate reviewed operations.
