# Inactive stock208 image installation candidate

`Dockerfile.stock-replay-208` is a separate, unpublished installation recipe for
original benchmark208b6b22/tree47b79175 and stock4.35.0-rc3. No existing workflow,
image recipe, Worker, controller, audit contract, admission or deploy setting
uses it. The other three retained stock configurations and all twelve pending
Results remain in scope and unqualified. This first configuration includes
private historical rows, but this recipe supplies no private Source support.

The recipe consumes the existing original595611952-byte release archive,
SHA1526de16c0496f16b46a168f26c990af4e3796505273c3342fec17d72d763997,
from the fixed build-context path. The consumed
`configuration/stock-image-208-public-inputs-v1.json` records the original public
download receipt's exact URL/size/SHA-256 and its own SHA-256. That receipt points to
`https://github.com/leanprover/lean4/releases/download/v4.35.0-rc3/lean-4.35.0-rc3-linux.tar.zst`.
It extracts the whole unmodified release: Lean/Lake470d5ce, its bundled comparator,
exporter, native kernel and NanoDa4c544ed. No custom comparator/NanoDa binary,
compiler shim, strip step, Lean/Lake patch or Nix runtime prefix is copied.
The five executable hashes were derived by streaming those five regular `bin/`
members from the exact SHA-256-verified original tarball, then are checked during
installation. Original `src/lake/Lake/CLI/Check.lean` Git bytes are separately
compared with the packaged release file. The original download receipt records
no download timestamp; file mtimes are not substituted for one. No upstream
signature, signed-URL freshness or new release authentication is claimed.

The base is the existing digest-pinned Sandbox0.12.7 Python image. The fixed
wrappers retain the base's original prebuilt Python3.11.14 at
`/usr/local/python/bin/python3.11`, selected through its existing
`/usr/local/bin/python3.11` alternative. Build checks the realpath/version before
apt, restores that exact alternative after any transitive distro Python install,
and checks again. It explicitly installs no distro Python/pip or new
interpreter/builder. Apt Meson uses its own transitive Ubuntu3.10 interpreter
explicitly, so restoring the native3.11 alternative does not redirect that
distro tool away from its `mesonbuild` packages. Instrumentation and pip retain
the base's original native3.11 throughout.
The same native Python/pip installs only the existing hash-locked requirements
with isolated pip, no dependency resolution and a verified fixed native site
directory. Named locked packages may replace existing versions; pip itself is
never upgraded. New build/check commands use `-I -B`, and source bytecode-cache
absence is checked. Bubblewrap0.12.0 is built from the same
SHA9760d007-bound source tarball as `install_bwrap.sh`, without changing its source
or loading/weakening a host AppArmor policy during build. Apt package resolution,
the native loader/compiler closure and nested user namespaces still need actual
image/runtime evidence. The official0.12.0 Git tag advertises peeled target
2a76602a8c71f36c1527cf9fc3417d9149822e0c; this does not prove the release source
tarball equals a Git archive. Profile binding must retain both that association
and the built release-source/binary/image hashes. No new
profile3 or image digest is invented from source installation.

The build-only `fetch-public-git` action obtains fixed original benchmark and
Lean Git commits plus the twelve dependency commits/URLs derived from the exact
original manifest. It uses only explicit public HTTPS routes, a minimal environment,
empty credential helpers, no prompt/hooks/replace objects and the existing owned
command primitive, with canonical40-hex revisions checked before fetching.
Shallow depth1 bare repositories retain each pinned commit's complete tree,
not its full history. The existing local dependency clone/checkout path is
separately tested with a genuine shallow bare fixture. Repositories remain at
fixed paths (root ownership is required by this inactive root caller):

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

The recipe preserves the pinned Cloudflare base's existing Sandbox startup
ENTRYPOINT/CMD, so later reviewed provider use does not need a rebuild just to
restore the SDK. This unused image is not deployed and no consumer/admission is
enabled. Installation CI explicitly overrides that startup to run the fixed
finite offline `check-installed` command and exit. The check verifies
actual original Git selections/dependency pins, all312 manifests/ordered296 plan,
the stock Check.lean bytes against original Lean Git, five binary hashes, original
Lean launcher/version and closed schema imports. It computes/reports actual
instrumentation and bwrap hashes, without comparing those with expected pins.
Those reports are observations for later profile binding, not authentication.
Its JSON explicitly reports `generation_observed=false`,
`image_qualified=false`, `runtime_qualified=false`, `request3_admitted=false` and
`private_Source_supported=false`. This is installation evidence only. It neither
constructs a Task, overlays Source, starts the supervisor, elaborates Lean Source,
starts the inherited server nor changes State/publication. The explicit networknone check
does not exercise the fresh preparer: its original cache fetch/restore phases
require separately authorized outbound public HTTPS. The actual host must also
support pidfds and readable own `/proc/self/task/*/children` lists.

After separate Root build authorization, from this exact source worktree:

```sh
mkdir -p .stock-image-inputs
cp /tmp/lean-eval-435rc3-checker-compat/lean-4.35.0-rc3-linux.tar.zst .stock-image-inputs/
DOCKER_BUILDKIT=1 docker build --file Dockerfile.stock-replay-208 --tag lean-eval-stock208:local .
docker run --rm --network none --entrypoint /usr/bin/python3 lean-eval-stock208:local -I -B /opt/lean-eval/stock_image_208.py check-installed
```

These are proposed commands, not executed results. BuildKit is required for the
per-Dockerfile Dockerignore; a legacy engine is not an acceptable build route.
The Dockerignore exposes only
the named public archive, required modules/wrappers/schemas and hash-locked Python
requirements and the fixed consumed public-input record. Do not commit the staged
archive to Git. Do not pass credentials, private Source, build secrets, host runtime
prefixes, arbitrary task inputs or Docker sockets. The build necessarily obtains
public Git/dependency sources and pinned bwrap/Python packages; the explicit check
uses no network and never starts the server. Do not publish the tag or wire it
into a Worker/controller. A plain default container start retains the base SDK
behavior; it is not an installation-check invocation or provider qualification.

Before real replay, independently establish a retained immutable image digest,
original driver and both-kernel positive/negative behavior, exact seven measured
phases/original argv, native loader/compiler closure, nested namespace/network and
private-control protection, actual CPU/counters/memory/declared M/T behavior,
fresh per-problem C/P reproducibility, authentic Task/Source/archive authority,
private-source integration and provider destruction/job/scratch cleanup. Requests3
remain rejected by active readers until that separate integration is reviewed.
