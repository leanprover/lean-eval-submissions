# Historical public adjudicated-evidence rerun

This record supersedes the public classifications in the earlier GitHub-evidence
aggregates. Those aggregates remain immutable historical evidence. This rerun
adds the reviewed legacy-adjudication registry and changes classifications
only; it is not a replay verdict and does not authorize intake, replay,
publication, AWS, State, Results, or release writes.

## Reproducible boundary

All 16 retained shards ran sequentially on 2026-08-25 from immutable dispatch
commit `ba5f5784427621f8b9be7396dd45a0938792707d`, protected `main`, after exact-main
CI and immutable-tag promotion succeeded. The independent protected Worker
rollout subsequently succeeded with all enablement skipped; it was not a
precondition for this source-free evidence lane. The workflow regenerated and
bound:

| Input | SHA-256 / count |
| --- | --- |
| historical inventory | `1a747133bba3c9ce09852967b4f3b4707bad64506890e4581bbf6f90a9be330c` |
| public resolution requests | `bf78ab88b8612c3aa1d627eb9efdda4c0989ef4d55451e706f825108e22f37de` |
| reviewed workflow-definition registry | `b9004ee87f0ff032e78198e251b87fe1bb1d0baaf77d6ea853335dd1f5487108` |
| reviewed legacy-adjudication registry | `4df6682b0e8b0ff129235c286aebf3322f37b002c846cc9fc8b14c054acf4ed1` |
| canonical results store | `14e8c8682e5183d85fee32aafcf06eedb20d7cd8aa91d666d50753d516da7d43` |
| inventory coverage | 1,301 results: 633 public-source probes and 668 private-archive migrations |
| public request coverage | 315 submission-grouped requests covering all 633 public results |

The downloaded shards were aggregated twice byte-identically with
`scripts/aggregate_public_replay_github_evidence.py`. The permanent,
schema-version-1, source-free aggregate is
[`evidence/historical-public-replay-github-evidence-ba5f578.json`](../evidence/historical-public-replay-github-evidence-ba5f578.json),
848,771 bytes with SHA-256
`ba816b52558cf77bd202618f820ffa6294ca2167698c94ab1096a39375c50212`.

## Classification result

| Classification | Requests | Accepted results | Meaning |
| --- | ---: | ---: | --- |
| `resolved` | 128 | 194 | Exact historical acceptance and currently available public source matched; eligible for exact-pin replay planning. |
| `source_unavailable` | 187 | 439 | Exact historical acceptance matched, but the public source revision is unavailable; remains pending. |
| **total** | **315** | **633** | 128 requests / 194 results resolved; 187 requests / 439 results remain pending. |

There are zero source-probe or generic-probe indeterminate, timing-indeterminate,
workflow-contract-unreviewed, ambiguous, or evidence-missing requests. CI
materializes the exact `results/` blobs at source commit `ba5f5784`, regenerates
the inventory and request artifact, and asserts their digests and every
request/result classification count. The two additional resolved groups are
the reviewed legacy cases; the remaining `source_unavailable` classification
is not a permanent-unavailability decision.

Only these 128 resolved request groups may advance to a newly generated
exact-pin replay plan. Earlier seed plans and evidence remain valid for their
bound inputs but do not cover this complete resolved set.

## Shard provenance

The package digest is GitHub's digest of the artifact archive; the JSON digest
is the SHA-256 of the extracted evidence document. All artifacts expire on
2026-09-24; the checked-in aggregate and this table survive artifact expiry.

| Shard | Run | Artifact ID | Package SHA-256 | Evidence JSON SHA-256 |
| ---: | --- | ---: | --- | --- |
| 0 | [`32794597497`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32794597497) | `9544369269` | `2646415b53998c0e5e22c7bf88886c8c2319ffffd390ff7ebfa9ce8b6f18f823` | `c217e1e6d572845412f9b7e534c6ce160079038f87d33fb20c0663ecdddf2f64` |
| 1 | [`32794689342`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32794689342) | `9544407927` | `9a6d0b8cbd34b3bb76a58653dbe07d933b15b6330f042ef1276494233a36695d` | `4d3b84902a7c4496bfbb4b968545a2713eb0910b6866f9d99f532b1916b03182` |
| 2 | [`32794761017`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32794761017) | `9544429447` | `9f0dec1e2c989563cd946d7e0d123d19dd805d7b5653ad99655c9e314a03fd6c` | `5497b5a0ffd27de3dd74077ba4cf75826e87df0b1e56fdcc8a342f41e5367c2a` |
| 3 | [`32794821287`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32794821287) | `9544446290` | `720c312dc9b67b68808cc6f398960a6fdcad50a705f3031c4c10263d8411a6dc` | `ca2415a7d06d5f394f4da4f76ef055e65a2ad68e0781e5a0ed4ab77544d96f0b` |
| 4 | [`32794870251`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32794870251) | `9544469502` | `549b10479ca3463adce5ae4ef12a70e81db1d8ed4515fa5bb50e86fdc1428f6d` | `62bb89e53a75adf061ad50e40e7562fb7247a676ed10c136153efc1bcc3057d6` |
| 5 | [`32794946216`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32794946216) | `9544496553` | `c7fdb78ee291ffa5d0b68a7f08bc0830112b31b77e8abf2cbf534785f74f285f` | `8337960237d31f5f7ec86d00ddcb0853c5db37463ee91f902b8ca99399ea24ba` |
| 6 | [`32795028385`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32795028385) | `9544523557` | `8edc2fe53a2c5f8f755ffa5b6f84b9bed73db191cbc1276864c2043b4128952e` | `fb36f94e3bc9c32426d761a3b23beafe68606176c32d2cb5b121f34c64382b55` |
| 7 | [`32795097061`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32795097061) | `9544540802` | `72b71e7342bc7167b2818bc9d1fd4c2a4efefcac75cb2b6053741082687b48e7` | `6813afc470de454028efe2b5f2ea312c29ccaac1e3b67f011647d6acffb48d71` |
| 8 | [`32795145499`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32795145499) | `9544560736` | `17bb645b8f929d91309e54996d0226e863f03419fd476f997ea108bec580610d` | `8523d312da0b205a222710590a5d8a54f0c6021d3ca7c089503daf328491f1f9` |
| 9 | [`32795203195`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32795203195) | `9544580391` | `a1d2059881957391d69b11bf95bdcc35dfa9285b5f44e9d278fd21d984bd13ef` | `0d0dcff9868bb0f6c7f6fc02a3f33ed4a9527aca7442e32af0384f5374420dd3` |
| 10 | [`32795264193`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32795264193) | `9544609214` | `62cb8501146a87a8ad8326ea86374d734a375ddbc2c40d20e9d2994182046909` | `db943ed197ddfcf6cb2d81be0e3aad5b9e0f72117dc5801c786337b2a4af7ae5` |
| 11 | [`32795344516`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32795344516) | `9544630144` | `fe291fe4d025403f36cd927d9268b310aa305491d4281b709a4ddd1cd186aa79` | `b86e4832668c54310195529a3600dea19dac58dd4dad04d21aa95d73d2facb22` |
| 12 | [`32795403993`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32795403993) | `9544654681` | `429307de0dc668def6688d3164927647a15de4e5ba714cef04b98d76878663c4` | `98edf5ad4afd2b7f788a2512c4c9a07c822398beec58acc8547977f22219fc40` |
| 13 | [`32795475393`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32795475393) | `9544683080` | `5cc8893749a36eb247932ec644d8f53af174e925b2613a4475ebd51c979030f1` | `cc33d0a1f512267057fa9866ed91a3fab45390ac88cfc9fc1ef79c485264b957` |
| 14 | [`32795558309`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32795558309) | `9544705014` | `5f8115820af3e529616f322f68a8f9ae210e410c4d048eb2f20a1ac31560d24b` | `2fed7fc2c5ffd108a7aa3062a26a6b615c455b5aa481f8b88cceb22ead88cd08` |
| 15 | [`32795629591`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32795629591) | `9544731377` | `d96c29b50661c2874d7807251b45deea8d0447e6845d525bee3b288eb52a872e` | `cb34ad210341011fb7bc2f65a1e5c9ad17833e3aba76f749c87bc82e33f6855c` |

A later source recovery or adjudication must produce another immutable
aggregate rather than rewriting any retained evidence object.
