# Historical public GitHub evidence rerun

This record closes the first complete GitHub-evidence pass after the historical
split-workflow definitions were reviewed. It is evidence classification, not a
replay verdict and not authorization to enable intake, replay, or publication.

## Reproducible boundary

All 16 shards ran on 2026-08-24 from immutable dispatch commit
`5746f90e72e863d96d992938aea0609978d1560c`, which was protected `main` when
dispatched. The workflow independently regenerated and bound these inputs:

| Input | SHA-256 / count |
| --- | --- |
| historical inventory | `96f9b9f4950af3836c3cd10639c18c3a320348cf77b080e74daef2c0d30c2a10` |
| public resolution requests | `9eb418273c129781755a16cc28964391931a9f4203a0a8487ff246902c512656` |
| reviewed workflow-definition registry | `82eff4dce70c2fcb7f480522f4de1fb16884534ce5f9452032908bb299c12196` |
| canonical results store | `14e8c8682e5183d85fee32aafcf06eedb20d7cd8aa91d666d50753d516da7d43` |
| inventory coverage | 1,301 results: 633 public-source probes and 668 private-archive migrations |
| public request coverage | 315 submission-grouped requests covering all 633 public results |

The downloaded shard JSON was aggregated offline with
`scripts/aggregate_public_replay_github_evidence.py`. The permanent,
schema-version-1, source-free aggregate is
[`evidence/historical-public-replay-github-evidence-5746f90.json`](../evidence/historical-public-replay-github-evidence-5746f90.json),
833,796 bytes with SHA-256
`13a0d95bd00cda236198d49c830159cb5790c9352b2fb1c6e94e07ec42787ecf`.
Keeping this reviewed projection in Git avoids making the audit trail depend on
the shard artifacts' 30-day retention. The inventory and request artifacts are
not duplicated: their exact bytes are deterministically reproducible by
checking out the recorded source commit, and their hashes are bound into the
aggregate.

## Classification result

| Classification | Requests | Accepted results | Meaning |
| --- | ---: | ---: | --- |
| `resolved` | 69 | 135 | Exact issue, accepted workflow/comment, and currently available public source matched; eligible for replay planning. |
| `source_unavailable` | 184 | 219 | Exact historical acceptance matched, but the public source revision is no longer available; remains pending. |
| `source_probe_indeterminate` | 57 | 57 | A bounded source probe reached a rate, permission, size, redirect, or similar fail-closed boundary; remains pending and may be retried. |
| `timing_indeterminate` | 2 | 2 | The otherwise matching public evidence was outside the conservative timestamp window; requires adjudication. |
| `evidence_missing` | 3 | 220 | No unique qualifying public evidence was found; remains pending. |
| **total** | **315** | **633** | 69 requests / 135 results resolved; 246 requests / 498 results remain pending. |

There are zero `workflow_contract_unreviewed`, `probe_indeterminate`, or
`ambiguous` requests. In particular, the workflow registry removed the former
contract-review bottleneck without promoting any unresolved classification.
Only the 69 resolved request groups may advance to exact-pin public corpus
replay. The other classes must not be recorded as permanent unavailability
without their separately reviewed lifecycle evidence.

## Shard provenance

Every run completed successfully at attempt 1 and produced exactly one
source-free artifact. The package digest below is GitHub's digest of the
downloadable artifact archive; the JSON digest is the digest of the extracted
evidence document recorded by the aggregate. GitHub currently reports all
artifacts expiring on 2026-09-23.

| Shard | Run | Artifact ID | Package SHA-256 | Evidence JSON SHA-256 |
| ---: | --- | ---: | --- | --- |
| 0 | [`32718053904`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32718053904) | `9516684798` | `7ca786ae4dfa18a424c637397ab4c0d65277535e46339cbd8808494aabfd826c` | `8844139c4cc5d6998b914274b416c0239da8aa8c6a8469070855bdf71c7def17` |
| 1 | [`32718130355`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32718130355) | `9516711397` | `32388eedc74fae2bbf6f2bf993aa529913d49f29a0bdcbb7c8e74b1d2d933550` | `4ae78318feab7d636425862a71551589f84e89f4fc3498e77165708faed7c241` |
| 2 | [`32718215919`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32718215919) | `9516745043` | `c0114764ab2ee8b9171c0ca96e91d3d90e7f8ddaa0536b7735d0a996917a26b9` | `f22f405829b7b8e74fd4dceaf4c8c43a1261e1ede05a9cf5dead19523516316d` |
| 3 | [`32718308012`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32718308012) | `9516772869` | `a7c9693b6b9b0a1ae4d4b0f9c53dcd8a6654e82851330e527d43810e95a92032` | `276ae10ba6a5de3c1dc81931b6abc834ab9f6e2ad58ccf93ad662a1654cce0ae` |
| 4 | [`32718369229`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32718369229) | `9516798472` | `73af908d8a7479a38ba22d49349f492105452705a90fb8321987a58ecd9dea45` | `cd9555135b56c9a10a1f9f72deee491968f2f50cf37c5b782a8d58b6e950e05f` |
| 5 | [`32718457345`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32718457345) | `9516837670` | `07e6e3c6600c2b93dc5268ff8452e26de611f72d9397604731728a33c7cc85f7` | `2fc4ac65922e21219d72905a4120d2f6c524de427e2527cb212a179b993ba3e4` |
| 6 | [`32718571735`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32718571735) | `9516880589` | `05b60630e231f0400a777a32600dbcb43160d30aceb28cabe9a60bceb4d6d65c` | `a8e1f52a72b2d4c2b89b3080ff98436abaac5c7881aedf8d8806ba8129601644` |
| 7 | [`32718682344`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32718682344) | `9516914093` | `ba531d57213c7a58f8ae9a12124d374bebc70be27eac0aed74b7a7c7f4c6682a` | `7833a5e6da3c38a67f65e15c9dd23041e322bead53007e5191af35a933a3fff9` |
| 8 | [`32718764745`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32718764745) | `9516944647` | `e9220b943db13456cb42bd79fda95e65c076f9c0e617081edcb555db59e58da2` | `02845f489dbc77f9f82b662d4ca9a7330c7cbee59007435373b19c577eb9950b` |
| 9 | [`32718850690`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32718850690) | `9516968519` | `443fa20389204594245266f4ee336efa3c23497a42e2da12f83745ea6595ae42` | `1fa4babd8d5bac1e63d2dcf3e9eef185702f673136831f3407452958662cc09c` |
| 10 | [`32718907520`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32718907520) | `9516997427` | `9c3381bcb72113aa8f075b7e5d1d0b4014e47f552b936465aa05221dc2e43f9f` | `7a28aed012d42da2e9225c4815486d49674b1a370f29f6a173fbc993599ff34d` |
| 11 | [`32718991988`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32718991988) | `9517023732` | `4aca2851dcf59b3c4c25808ca09908f34af7ad8c11cb99d8deb4043640b3a636` | `5c47f288e75dd35d123f0110554ac2d8b74c45968584c513d0fb6101762cd1ea` |
| 12 | [`32719076707`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32719076707) | `9517061463` | `904c9f0a904e6e1af65cc76e20490e23cc776a82b7183c47546d62c7467f9da9` | `b044849e20d2fbdd6538533518015956f4229532ffe3fa56d9b78efa8eb85e1a` |
| 13 | [`32719166164`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32719166164) | `9517091920` | `937f6283ba42019203fc15278181fe73812937658faf5c6259514954d7123ccd` | `a1977e6a9cc5c1c9d65134b155b0474fa2f08ada81aa60d8a07bbef42cb35c9a` |
| 14 | [`32719255528`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32719255528) | `9517118518` | `29af2e7f62c70cf2ef2ef5a1d458cdbd4dbbd15915ed4888c309ef8c3155a4d7` | `dd56b6dbdab4f466c36d28970be8283dae187e2acb7c939309ecd98cd2b7fd63` |
| 15 | [`32719340876`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32719340876) | `9517159594` | `eeba0bb309ecad45f823d66882168a9d366bc6da31bef823f01b68d84ad82e13` | `d99118d72f8035cd5163d7a07445133f5b9892c5d7b1c46843e49aa7a37588a7` |

This table and the committed aggregate survive artifact expiry. A later probe
or adjudication must produce a new immutable aggregate rather than rewriting
this evidence object.
