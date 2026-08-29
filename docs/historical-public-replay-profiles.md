# Historical public replay execution profiles

The final adjudicated historical replay plan contains 35 exact benchmark commits over
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
credential. The reviewed corpus requires 35 images, not five: 21 v4.30.0-rc2,
3 v4.30.0, 3 v4.32.0-rc1, 5 v4.32.2, and 3 v4.33.0. Fifteen images need the legacy
monolithic `manifests/problems.toml` reader; the other 20 use per-problem
manifests.

The committed matrix has SHA-256
`a674707eea7a9556576c8dcbe57bcf6b4f44362d2bdfd47895fb7c783554f39c`.
It binds final plan `d6e81393…`, toolchain registry `4f2f3737…`, and component
lock `68b5a58c…`; all 35 entries remain explicitly `unqualified`.

All thirty-five current-matrix qualification objects are frozen under
`evidence/public-replay/profiles/`. Appending State qualification and enqueue
events remains gated on the exact immutable historical execution packet.

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

The first boundary is specified by
[`historical-public-runner.md`](historical-public-runner.md). Its controller
accepts only the exact reviewed plan, matrix, contract digests, request ID, and
result ID. It verifies exact public source and benchmark Git commits and trees,
creates a deterministic source archive, and hands a separate runner a closed
JSON object. The runner revalidates the matrix entry and baked benchmark tree,
actively checks that networking is disabled, and then invokes the fixed
evaluator command. This implementation does not change the private replay
request or endpoint. It also does not make any matrix entry qualified: the
immutable image publication and staging runtime evidence are still absent.

Only then may the runtime evidence be frozen into an execution profile. The
profile digest includes the unique image manifest digest, so the 35 benchmark
images produce 35 independently reviewed execution profiles even where their
Lean and checker component versions coincide. Each qualification object must
be committed at
`evidence/public-replay/profiles/<execution-profile-digest>.json`; appending
State qualification events and enqueue events remains a later, separately
reviewed action.

The fail-closed preparation and post-commit finalization boundary for that
later action is specified in
[`historical-public-authority-preparation.md`](historical-public-authority-preparation.md).
It consumes the exact version-2 qualification artifact ZIPs and remains blocked
until the frozen profile is reviewed and committed; it never writes State or
enqueues replay.

## Execution boundary

The shared replay request models a modern result and requires a UUID submission
ID. Historical public State tasks intentionally have no submission ID or
archive locator. The deployed authoritative Worker also accepts only a private
encrypted archive. The dedicated historical controller/runner therefore uses
`schemas/historical-public-runner-handoff-v1.schema.json` and does not alter or
reuse the private endpoint's request shape.

`Dockerfile.historical-public-replay` and
`historical-public-image-qualification.yml` now select exactly one unqualified
matrix entry, recover its locked build inputs, bake its benchmark markers, and
reject a caller-supplied commit that is not in the matrix. This satisfies the
per-entry build-selection contract but does not qualify any entry: registry
publication and the staging probe remain the separate requirements above.

Exact prerelease toolchains are now admitted by the shared execution-profile
validators, and the authoritative evaluator can read both historical and
current manifest layouts. Those compatibility paths and matrix-selected build
inputs do not themselves satisfy the immutable publication and staging-probe
requirements above.

## Local reconstruction

From a full public `leanprover/lean-eval` Git checkout:

```console
python scripts/prepare_historical_replay_profile_matrix.py \
  --plan evidence/public-replay/plans/d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e.json \
  --toolchain-registry evidence/public-replay/toolchains/4f2f3737d79e6abd6c169ebdde3f2218157d8f6c482a85ad2026821a4b8e81a0.json \
  --component-lock configuration/historical-public-replay-components-v1.json \
  --benchmark-repository /path/to/lean-eval \
  --output /tmp/historical-public-replay-profile-matrix.json
cmp /tmp/historical-public-replay-profile-matrix.json \
  configuration/historical-public-replay-profile-matrix-v1.json
```

The producer writes its output create-only and makes no network request. The
caller controls how the public Git objects are obtained.
