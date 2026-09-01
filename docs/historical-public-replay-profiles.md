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
lock `68b5a58c…`. The matrix remains a record of the pre-qualification build
inputs; all 35 corresponding qualified execution profiles are now frozen
under `evidence/public-replay/profiles/`.

The temporary workflow, controller, isolated Worker, container application,
and preparation lane used to create those profiles have been retired. Their
paths and digests remain inside the immutable profile objects as historical
provenance and are verified from the recorded Git commits. They are not live
instructions and cannot be dispatched from the current tree. Appending State
qualification and enqueue events remains gated on the exact immutable
historical execution packet.

## Retained authority boundary

The matrix deliberately remains `qualification_status: unqualified`: it is a
historical build input, not State authority. Authority comes only from the 35
committed `historical_public_replay_profile_qualification` objects. Each binds
one benchmark commit and tree, immutable registry manifest digest, runner and
measurement configuration, and the exact historical controller commits that
produced its evidence.

The retained offline `finalize-batch` command validates those objects and the
packet-pinned State contract before emitting a create-only append candidate.
It cannot build or qualify an image, deploy a Worker, write State, or enqueue
replay by itself. No command in the current tree creates an additional public
qualification profile.

## Execution boundary

The shared replay request models a modern result and requires a UUID submission
ID. Historical public State tasks intentionally have no submission ID or
archive locator. The deployed authoritative Worker also accepts only a private
encrypted archive. The dedicated historical controller/runner therefore uses
`schemas/historical-public-runner-handoff-v1.schema.json` and does not alter or
reuse the private endpoint's request shape.

`Dockerfile.historical-public-replay` retains the exact image recipe needed to
replay the already-qualified profiles. The retired qualification workflow is
available only through the immutable controller commits named by those
profiles; it is intentionally absent from the current tree.

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
