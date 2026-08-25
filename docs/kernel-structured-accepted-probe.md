# Structured-kernel accepted-result qualification

The manual `kernel-structured-accepted-probe.yml` lane is a staging-only bridge
between the historical public runner and the independent-kernel wire contract.
It is intentionally not a corpus run or a promotion decision. It proves one
narrow fact: an accepted result can run through Lean, comparator and
`replay-measure`, and the exact export captured by that completed attempt can
then receive the closed `accepted` result from Mathgraph's structured protocol.

## Fixed public target

The fixture selects issue 906's `two_plus_two` result:

- request `prr_632ee5…`, result `r2_c4e178…`, problem revision 1;
- public source `KitaKen1/lean-eval-two-plus-two` at `a7cf16ee…`, tree
  `b2ec7225…`;
- benchmark `leanprover/lean-eval` at `3f3786f3…`, Lean v4.32.2; and
- exact `generated/two_plus_two/config.json` digest `dd9f6978…`.

This is the smallest target already bound by the immutable historical replay
plan and already reproduced as accepted by `public-replay-smoke.yml`. Choosing
it avoids synthetic source, result, State or queue identities while exercising
the actual accepted-result path. The committed fixture also binds the plan,
profile-matrix and prior smoke-fixture bytes.

## Current hard block

Mathgraph PR #51 is still unmerged. The probe therefore pins its exact
provisional head `400ab9c1…`, source tree `05ebd1a6…`, schema and vector
digests, and records `blocked_on_unmerged_upstream`. The dedicated derived
Dockerfile may be reviewed and its builder may be tested locally, but the
manual workflow calls `require-runnable` before fetching target source or
building an image. That call must fail at the committed status.

Do not turn the fixture status green merely because the fork commit builds.
After upstream merge, review the resulting upstream history, then update all of
the following together:

1. PR-head, candidate commit/tree and protocol-file digests if upstream changed
   them;
2. `build_repository` and the fixed Docker remote to
   `metalogiclabs/mathgraph-lean-kernel`;
3. `upstream_status` to `merged_upstream_exact_commit`; and
4. `qualification_status` to `ready_for_staging_probe`.

The workflow independently fetches the exact candidate from upstream, proves
it is an ancestor of the PR's exact upstream `v2-arena-candidate` base branch,
checks PR #51's upstream repository, base, merged record and exact head, and
requires an immutable protected submissions dispatch tag.

## Attempt and execution boundary

The controller fetches only the two reviewed public commits and uses the
existing historical runner controller to create the exact public source
archive and closed handoff. A fresh random nonce, handoff digest, archive
digest/size, runner source commit, local image ID, and measured candidate binary
digest produce a `kpa1_…` attempt ID before execution.

Execution uses a separate derived image and Docker `--network none`; the
publishable historical image remains unchanged. The runner actively probes
that network access fails and rejects credential-shaped environment variables.
It removes any incumbent export before evaluation, waits for the fixed public
result to complete as accepted, reads the capture through a no-follow bounded
regular-file check, validates the format-3.1.0 NDJSON, and invokes exactly:

```text
/opt/lean-eval/bin/sokonanoda --result-file \
  /run/lean-eval/kernel-output/sokonanoda-result.json \
  /run/lean-eval/nanoda-config.json
```

The candidate receives an empty environment and the canonical config derived
from the exact benchmark config. Before invocation the runner destroys the
expanded public source and evaluator output. Landrun then grants the candidate
read access only to its binary/runtime, exact export and exact config, plus
write access to its isolated result directory; the still-mounted controller
input and baked benchmark are outside that closed policy. Only exit 0 plus the exact canonical
`sokonanoda_result_v1` `accepted`/`checked` record succeeds. Missing,
noncanonical, contradictory, rejected, declined and internal-failure results
all fail the probe.

## Evidence and non-authority

The uploaded artifact contains identities, digests, counts, bounded stream
digests, the source-free historical verdict, the export's reported
exporter/Lean/format identity, runtime isolation claims, and the structured
result. It never contains source files, archive bytes, export bytes, checker
stdout/stderr, evaluator output files, credentials or configuration contents.
The raw capture is consumed before the runner's `finally` cleanup and never
leaves the network-disabled container.

This lane has read-only GitHub permissions and no OIDC, Cloudflare, AWS, State,
Results, enqueue, registry, deployment, release or promotion interface. Its
attestation remains `qualification_status: provisional`; one accepted target
does not qualify the full checker series, rejection semantics, historical
corpus, image publication, or durable replay handoff.

Local contract checks are safe while the upstream gate remains blocked:

```console
python scripts/kernel_structured_accepted_probe.py validate-fixture \
  --fixture tests/fixtures/kernel-structured-accepted-probe-v1.json \
  --plan evidence/public-replay/plans/d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e.json \
  --matrix configuration/historical-public-replay-profile-matrix-v1.json \
  --smoke tests/fixtures/public-replay-smoke-v1.json
python scripts/kernel_structured_accepted_probe.py require-runnable \
  --fixture tests/fixtures/kernel-structured-accepted-probe-v1.json
```

The second command must currently fail with the PR #51 blocker.
