# Inactive stock208 image installation candidate

`Dockerfile.stock-replay-208` is a separate, unpublished installation recipe for
original benchmark208b6b22/tree47b79175 and stock4.35.0-rc3. No existing workflow,
image recipe, Worker, controller, audit contract, admission or deploy setting
uses it. The other three retained stock configurations and all twelve pending
Results remain in scope and unqualified. This first configuration includes
private historical rows, but this recipe supplies no private Source support.

The recipe consumes the existing original595611952-byte release archive,
SHA1526de16c0496f16b46a168f26c990af4e3796505273c3342fec17d72d763997,
from the fixed build-context path. The saved public download receipt points to
`https://github.com/leanprover/lean4/releases/download/v4.35.0-rc3/lean-4.35.0-rc3-linux.tar.zst`.
It extracts the whole unmodified release: Lean/Lake470d5ce, its bundled comparator,
exporter, native kernel and NanoDa4c544ed. No custom comparator/NanoDa binary,
compiler shim, strip step, Lean/Lake patch or Nix runtime prefix is copied.
The five executable hashes are additionally checked against the original archive.
The checked hash and prior public acquisition evidence are the artifact binding;
this source change performs no new upstream release authentication.

The base is the existing digest-pinned Sandbox0.12.7 Python image. The fixed
wrappers require `/usr/bin/python3`3.11; build refuses a different system Python
instead of changing their argv/shebang/runtime policy. Python dependencies use
the existing hash-locked requirements. Bubblewrap0.12.0 is built from the same
SHA9760d007-bound source tarball as `install_bwrap.sh`, without changing its source
or loading/weakening a host AppArmor policy during build. Apt package resolution,
the native loader/compiler closure, bubblewrap's source Git commit for profile3,
and nested user namespaces still need actual image/runtime evidence. No new
profile3 or image digest is invented from source installation.

The build-only `fetch-public-git` action obtains fixed original benchmark and
Lean Git commits plus the twelve dependency commits/URLs derived from the exact
original manifest. It uses only explicit public HTTPS routes, a minimal environment,
empty credential helpers, no prompt/hooks/replace objects and the existing owned
command primitive. Full bare Git objects remain at fixed paths:

- `/opt/lean-eval/git/benchmark.git`: original208/tree47b.
- `/opt/lean-eval/git/lean.git`: original470d5ce/tree062f40e3.
- `/opt/lean-eval/git/dependencies/<original-name>.git`: original manifest pins.
- `/opt/lean-eval/toolchain`: complete original stock release.

Unlike the legacy image, original Git provenance is retained. No root benchmark
build cache or generated workspace is imported. Missing public dependency caches
may make fresh bounded preparation fail; no supplied cache receipt, cached output,
dead OwnedGeneration or larger timeout is accepted as a replacement. A future
reviewed runtime caller can use these local Git repositories with the unchanged
preparer API. It must genuinely generate a fresh selected workspace, run the
complete original296-module semantic inventory within1200s with four owned slots,
selected generation300s, strict pre-Source C freeze and detach300s/caps. This image
check does not execute or shorten that work. Cold9's controlled host result, when
available, will not by itself qualify these image paths/resources/runtime.

Existing stock measurement/supervisor/preparer/contract modules and their imports
are installed at `/opt/lean-eval/*.py`. The three existing stock entries and the
unchanged `replay-measure` primitive are at their existing fixed paths under that
directory. The flat contract module's unchanged `parent.parent/schemas` lookup
requires the four closed schemas at `/opt/schemas`; the installation check proves
that layout and the offline registry. No module source or C/P schema is patched
to accommodate installation. `/run/lean-eval` remains a private0700 control root.

The default entry runs a finite offline `check-installed` and exits. It verifies
actual original Git selections/dependency pins, all312 manifests/ordered296 plan,
the stock Check.lean bytes against original Lean Git, five binary hashes, original
Lean launcher/version, instrumentation hashes, closed schema imports and bwrap
file identity. Its JSON explicitly reports `generation_observed=false`,
`image_qualified=false`, `runtime_qualified=false`, `request3_admitted=false` and
`private_Source_supported=false`. This is installation evidence only. It neither
constructs a Task, overlays Source, starts the supervisor, elaborates Lean Source,
creates a service nor changes State/publication.

After separate Root build authorization, from this exact source worktree:

```sh
mkdir -p .stock-image-inputs
cp /tmp/lean-eval-435rc3-checker-compat/lean-4.35.0-rc3-linux.tar.zst .stock-image-inputs/
docker build --file Dockerfile.stock-replay-208 --tag lean-eval-stock208:local .
docker run --rm --network none lean-eval-stock208:local
```

These are proposed commands, not executed results. The Dockerignore exposes only
the named public archive, required modules/wrappers/schemas and hash-locked Python
requirements. Do not pass credentials, private Source, build secrets, host runtime
prefixes, arbitrary task inputs or Docker sockets. The build necessarily obtains
public Git/dependency sources and pinned bwrap/Python packages; the default run
uses no network. Do not publish the tag or wire it into a Worker/controller.

Before real replay, independently establish a retained immutable image digest,
original driver and both-kernel positive/negative behavior, exact seven measured
phases/original argv, native loader/compiler closure, nested namespace/network and
private-control protection, actual CPU/counters/memory/declared M/T behavior,
fresh per-problem C/P reproducibility, authentic Task/Source/archive authority,
private-source integration and provider destruction/job/scratch cleanup. Requests3
remain rejected by active readers until that separate integration is reviewed.
