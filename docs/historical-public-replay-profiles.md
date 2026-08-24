# Historical public replay execution profiles

The protected historical replay plan contains 25 exact benchmark commits over
five Lean toolchains. An authoritative image bakes a benchmark commit, its
generated workspaces, dependency cache, exact Lean toolchain, checker tools,
and a profile lock. A toolchain-only image would therefore be false authority:
two benchmark commits using the same Lean release still have different trusted
problem statements and generated workspaces.

`configuration/historical-public-replay-profile-matrix-v1.json` is the
deterministic, source-free build-readiness matrix. Its producer validates the
exact committed plan and toolchain-registry bytes, then reads only immutable
Git objects from a caller-supplied `leanprover/lean-eval` checkout. For every
binding it verifies:

- the commit and tree identities and exact `lean-toolchain` blob digest;
- the presence of the trusted evaluator sources and root Lake manifest;
- every planned problem's statement revision and generated workspace; and
- the exact component lock, including a toolchain-matched `lean4export`
  commit.

The output retains hashes, profile locks, counts, manifest layout, and planned
problem IDs. It contains no submission source, issue body, result source,
credential, archive locator, Cloudflare account identifier, or image-registry
credential. The reviewed corpus requires 25 images, not five: 12 v4.30.0-rc2,
3 v4.30.0, 3 v4.32.0-rc1, 5 v4.32.2, and 2 v4.33.0. Six images need the legacy
monolithic `manifests/problems.toml` reader; the other 19 use per-problem
manifests.

## Qualification boundary

The matrix deliberately says `qualification_status: unqualified`. A matrix
entry is a build input, not a State qualification object. It cannot be used as
`historical_result.replay_profile_qualified` evidence until all three named
requirements are complete:

1. `historical_public_runner_v1`: a reviewed runner/controller contract must
   fetch one exact public commit before the untrusted network-disabled phase,
   bind the fetched tree, and execute the separate historical public queue
   shape without inventing a submission UUID or private archive.
2. `immutable_registry_publication_v1`: one image per exact benchmark commit
   must be built from the matrix lock, verified offline, pushed under a
   create-only tag, and resolved to an immutable manifest digest.
3. `cloudflare_staging_runtime_probe_v1`: each immutable image must prove its
   exact baked benchmark/toolchain lock, fixed runner command, network
   isolation, destruction, architecture, kernel, CPU, and memory boundary in
   staging.

Only then may the runtime evidence be frozen into an execution profile. The
profile digest includes the unique image manifest digest, so the 25 benchmark
images produce 25 independently reviewed execution profiles even where their
Lean and checker component versions coincide. Each qualification object must
be committed at
`evidence/public-replay/profiles/<execution-profile-digest>.json`; appending
State qualification events and enqueue events remains a later, separately
reviewed action.

## Known contract work before execution

The shared replay request currently models a modern result and requires a UUID
submission ID. Historical public State tasks intentionally have no submission
ID or archive locator. The deployed authoritative Worker also accepts only a
private encrypted archive. The public runner must therefore have a distinct
closed request/handoff contract rather than weakening the private endpoint or
synthesizing modern lifecycle fields.

The current authoritative Dockerfile, publication workflow, and configuration
freezer are also one-profile implementations: they hard-code benchmark
`b91d4757…`, Lean v4.33.0, its v4.33.0 `lean4export`, and 309 generated
workspaces. They must be generalized behind the exact matrix entry (or replaced
by generated per-entry inputs) before any historical image is published. A
caller-supplied build argument without matrix verification is not a
qualification boundary.

Exact prerelease toolchains are now admitted by the shared execution-profile
validators, and the authoritative evaluator can read both historical and
current manifest layouts. Those compatibility changes remove two build-time
gaps; they do not satisfy the three qualification requirements above.

## Local reconstruction

From a full public `leanprover/lean-eval` Git checkout:

```console
python scripts/prepare_historical_replay_profile_matrix.py \
  --plan evidence/public-replay/plans/2b00c9651f5c3f43d44e0306a8368947a4a950ab3dd1e8c9b1f283fc82101942.json \
  --toolchain-registry evidence/public-replay/toolchains/5144fc19bbbbcf0ef16a1d7c88b163254f96a250cb4a5846fbbb0d465ce16790.json \
  --component-lock configuration/historical-public-replay-components-v1.json \
  --benchmark-repository /path/to/lean-eval \
  --output /tmp/historical-public-replay-profile-matrix.json
cmp /tmp/historical-public-replay-profile-matrix.json \
  configuration/historical-public-replay-profile-matrix-v1.json
```

The producer writes its output create-only and makes no network request. The
caller controls how the public Git objects are obtained.
