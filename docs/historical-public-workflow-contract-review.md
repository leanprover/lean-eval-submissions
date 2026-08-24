# Historical public workflow contract review

This review registers the public split-repository evaluator revisions found by
the first complete historical evidence pass. It is a review of the full
`.github/workflows/submission.yml` dataflow, not an inference from protected-main
ancestry or a search for isolated YAML fragments.

## Review boundary and evidence

The registry change is based on the merged resolver contract at submissions
commit `d5d7e8487cecf4faac8fa9afa6426cc6cf931c4e`. At review time the public Git
remote and GitHub branch metadata both reported that exact commit as protected
`main`. Every registered evaluator commit is an ancestor of that commit.

The source-free evidence was reproduced from the resolver-introduction commit
`5fee3484251dcc000d2a92fae1aa2cd875355667`:

- historical inventory SHA-256
  `118e0b61406f6f8e550cfeb41515dba244001cecebb9261b876b2d867381855a`,
  covering 1,301 accepted results (633 public and 668 private) at canonical
  results-store digest
  `14e8c8682e5183d85fee32aafcf06eedb20d7cd8aa91d666d50753d516da7d43`;
- public resolution-request SHA-256
  `3d693e1da482d4c2910c09b85fb2af37552f2f4dfcc50ecfed5e8d3386ee23c3`,
  covering 315 submission-grouped requests and all 633 public results;
- sanitized evidence SHA-256
  `0b1ec7243fc5e85f368a9b0c75f7511dc3365e97993c937dccfc7b27b8e1a3b2`,
  produced against the empty-registry digest
  `39948ea4c090b5d2c49ac6ac721e22132dd0f8589b99b0b4ccc2ec377f810c54`.

The inventory and request set were also generated twice from the exact review
base. Their SHA-256 values are
`1a8b41f4ee6b13fec172249760db94dee70ef2e6413710f33ba6a019d58274a2`
and `44643a091d5b11ec095fc17812828cb4a2543cf235bcc376d50a02240d1f97d9`
respectively. After removing only the expected `source_commit` and derived
`inventory_sha256` bindings, both are identical to the resolver-introduction
artifacts, including every request and result identity.

The inventory and request generator each produced byte-identical first and
second outputs. Joining the evidence back to the request set found exactly 224
`workflow_contract_unreviewed` requests, containing 427 accepted results and
119 distinct evaluator-commit/definition tuples. Those tuples reduce to the 12
complete workflow bodies below.

## Semantic audit

For every body, the review followed the active jobs and conditions from the
benchmark checkout through the accepted record. Eleven bodies use the direct
path: the evaluation job checks out `leanprover/lean-eval` at `main` into
`lean-eval`, resolves `git -C lean-eval rev-parse HEAD` into the `benchmark`
step's `sha` output, exposes it unchanged as the evaluation job's
`benchmark_commit`, and passes that output unchanged through the record step's
`BENCHMARK_COMMIT` to `update_leaderboard.py --benchmark-commit`. The evaluator
reads its generated workspaces, problem manifest, repository root, and shared
packages from that same checkout.

The body at digest `2e50c16f…` uses a two-stage freeze with the same contract.
Its archive job checks out `leanprover/lean-eval@main`, resolves the SHA and
toolchain, and exposes both as job outputs. The evaluation job checks out that
exact SHA, fails unless both the actual SHA and toolchain equal the archive
outputs, then exposes the verified actual SHA. The record job passes that
evaluation output unchanged to `update_leaderboard.py --benchmark-commit`.

| Definition SHA-256 | Example evaluator commit | Tuples | Requests | Results | Dataflow |
| --- | --- | ---: | ---: | ---: | --- |
| `1d0650f3821f066e93f0b8432b2cecad43ffd8c0a9a66c3eb977422aeb356587` | `0995de7b0c1dee33be1f7cbceebe0d400363d842` | 4 | 6 | 8 | direct |
| `2e50c16f2244c4e0d2811215287dd30ff0db62b99e5f34617064236f7f8a5c0e` | `f1ec5540b2f3537fa61d66079929d5dc6d66f734` | 1 | 1 | 1 | two-stage freeze |
| `3dc721027f5c5560a6de1453971bfd078fc65c821e81f8b05ff4ed10ef0eba3a` | `2220b0f2b339ee2dc005d16a19f63b0b64d6ae7c` | 1 | 1 | 1 | direct |
| `7a40ba508f8da9ace2ac2bcb487274f7ada0f461744a700c9cda1a86077b379e` | `01f64b97180801f6da7da10e61ee54c68e3c6bb7` | 14 | 18 | 18 | direct |
| `7b15e22709ba65f73fcc953a778fd1de7d95552e25c52be9fa99693089b573f5` | `1021dba0a1d78b07cb03a7e4e4d7163ef4083a37` | 6 | 8 | 10 | direct |
| `7b5794fd605ae7fcbb4e9b3984414d364c0b3081a0f87dd0ec90fa5973aed0e0` | `1221b841ffc41a50d696129dae280308d3f3970d` | 2 | 3 | 3 | direct |
| `ba279a2230ed38d075c162835dbc67455104bd2ec513db459da8a17ffa3cfa40` | `6b3be460bdebb24610bfe2ba48f77a23c5211095` | 1 | 1 | 1 | direct |
| `ba851933ac8a333fc9cc26eea813a82ffb585069b682ac28d123cd0214cc679b` | `0e1cb218b0e715963b51f3b2d624b8e505181cfa` | 14 | 17 | 27 | direct |
| `d8685a08f6a4c5e6b95913e6f906ef4a9364cce360dc862841118d56dc773c1a` | `177847f5b1b59c07c63b024a2ab0d7af99cfd623` | 19 | 20 | 33 | direct |
| `e14dd7a28ca829e17132da4267e8b16b1db564bfa853296a1142022989a91a23` | `07cd096f4f3953e2fa3f8735082bbeab818b6429` | 4 | 4 | 5 | direct |
| `f851886da7ca7caf8616355531e0aedf99dee9bb5e262f18e4cce751e8df1a7a` | `bcc8fdef486e013f1edf6654280833912ed6b22d` | 2 | 2 | 22 | direct |
| `fa0889fb632a5856ec8c86a87c2c3e4f305409d927eb31b492198ea3990fbd1a` | `00730f4226ee4f4818abcdf38033b368a247d63b` | 51 | 143 | 298 | direct |

For all 119 tuples, the complete historical blob recomputed to the digest in
the evidence. No tuple had a missing Git object, a digest mismatch, or a commit
outside protected-main ancestry. No body had an ambiguous or unsafe benchmark
dataflow, so this review leaves no workflow-contract tuple unregistered.

Registration removes the workflow-contract gate for exactly 224 requests and
427 accepted results. It does not itself change their evidence classification:
the affected public evidence shard must be rerun so that the resolver can
continue through result-comment and source probes. No workflow was dispatched
as part of this review.
