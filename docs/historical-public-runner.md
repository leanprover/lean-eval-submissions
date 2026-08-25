# Historical public runner contract

`historical_public_runner_v1` separates trusted public-object preparation from
untrusted execution. It is deliberately independent of the deployed private
archive replay endpoint.

## Controller boundary

The controller consumes one exact request/result pair from the canonical
historical plan. An operator must supply the reviewed SHA-256 values of the
plan, profile matrix, and runner contract. Before constructing a handoff, the
controller:

1. requires canonical, schema-valid plan and matrix bytes;
2. verifies the matrix binds those exact plan bytes and remains unqualified;
3. resolves the source and benchmark as exact commits from their exact public
   GitHub repository remotes;
4. verifies both Git tree IDs, the benchmark tree recorded by the matrix, and
   the exact `lean-toolchain` blob; and
5. creates a bounded `git archive` of the public source under a fixed `source/`
   prefix and reconstructs its Git tree ID from the archive before handoff.

The reconstructed-tree check rejects `export-ignore`, `export-subst`, unsafe
file types, or any other archive transformation that would make replay input
differ from the recorded source tree. Tar modes, member prefix, and gzip header
metadata are normalized; the exact archive digest is bound to its handoff.

The resulting `historical-public-runner-handoff-v1` object contains only public
repository identities, hashes, the historical result identity, and the exact
unqualified profile lock. It has no submission UUID, private archive locator,
credential, source text, environment value, or publication authority.

## Runner boundary

The separate `/opt/lean-eval/historical-public-runner` entrypoint accepts fixed
workspace paths. It revalidates the contract and matrix byte digests, the
canonical matrix entry digest, the source archive digest and safe member set,
the reconstructed source Git tree, and three baked benchmark markers: exact
commit, exact tree, and exact `lean-toolchain` bytes. The image build must create
`.lean-eval-commit` and `.lean-eval-tree` from the matrix-verified Git object
rather than from caller supplied build arguments.

After all inputs are closed, the runner actively probes that outbound network
access fails before inspecting or executing source. It exposes an empty
untrusted environment and invokes the fixed evaluator, fixed nanoda checker,
fixed measurement command, and fixed limits. Its verdict uses historical
request/result IDs and never synthesizes a modern lifecycle UUID.

## Contract-only workflow

`historical-public-runner-contract.yml` is a manually dispatched dry run. One
job fetches the exact public source and benchmark commits and uploads the
closed handoff plus deterministic source archive. A separate job downloads
that handoff and validates it inside a read-only container launched with
`--network none`.

The workflow has read-only repository permissions, one-day artifact retention,
and no environment, cloud credential, State writer, registry publication,
deployment, replay, or release step. It cannot qualify a profile or append a
State event.

## Delivery state

This contract-only change deliberately does not install the historical runner
into `Dockerfile.replay-authoritative` or publish a runnable image. The 35
matrix-derived image builds are a separate gated lane: each must copy the
historical entrypoint, validator, contract, and exact matrix into the image and
bake both benchmark markers from the fetched Git object. Until those immutable
images pass their per-image Cloudflare runtime probes, every profile remains
`unqualified` and no historical replay may be enqueued.

## Local preparation

With exact public source and benchmark checkouts whose `origin` remotes are the
canonical HTTPS GitHub URLs:

```console
python scripts/historical_public_runner.py prepare \
  --plan evidence/public-replay/plans/d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e.json \
  --profile-matrix configuration/historical-public-replay-profile-matrix-v1.json \
  --contract configuration/historical-public-runner-v1.json \
  --expected-plan-sha256 d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e \
  --expected-profile-matrix-sha256 a674707eea7a9556576c8dcbe57bcf6b4f44362d2bdfd47895fb7c783554f39c \
  --expected-contract-sha256 6d341a642dfd6aa9092228269da6761000bf0818128ce3f35cb259bd8fb2303f \
  --request-id prr_<64-lowercase-hex> \
  --result-id r2_<64-lowercase-hex> \
  --source-repository /path/to/exact-public-source \
  --benchmark-repository /path/to/lean-eval \
  --source-archive /new/path/historical-public-source.tar.gz \
  --output /new/path/historical-public-request.json
```

Both outputs are create-only. Their production use remains blocked on the 35
immutable matrix-derived images and the complete Cloudflare staging runtime
probe for each immutable manifest digest.
