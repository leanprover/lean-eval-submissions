# Historical public Gist probe rerun

This record supersedes the public-Gist classifications in the first complete
GitHub-evidence pass. The earlier aggregate remains immutable historical
evidence. This rerun changes only public-read classification; it is not a replay
verdict and does not authorize intake, replay, publication, AWS, State, Results,
or release writes.

## Reproducible boundary

All 16 retained shards ran on 2026-08-24 from immutable dispatch commit
`6c13c245d17a1e25a59846769e533265e8ac9ba8`, protected `main`, after immutable
tag promotion and exact-main CI succeeded. The workflow regenerated and bound:

| Input | SHA-256 / count |
| --- | --- |
| historical inventory | `7c1b393711654741a6d69d5c0e8db02cf89078c4cc5fe3e96002c614d5c0bd22` |
| public resolution requests | `50202e7331a77ed04be04a784315b8ecfad6f593edc6686763d196552df5e2fa` |
| reviewed workflow-definition registry | `82eff4dce70c2fcb7f480522f4de1fb16884534ce5f9452032908bb299c12196` |
| canonical results store | `14e8c8682e5183d85fee32aafcf06eedb20d7cd8aa91d666d50753d516da7d43` |
| inventory coverage | 1,301 results: 633 public-source probes and 668 private-archive migrations |
| public request coverage | 315 submission-grouped requests covering all 633 public results |

The shards were run sequentially because they share GitHub API budgets and a
one-pending-run concurrency boundary. One later duplicate shard-2 dispatch,
run `32769243324`, was cancelled before use; retained shard 2 is run
`32769239884`. Every retained shard completed successfully and has zero
source-probe, generic-probe, rate/permission, or unreviewed-workflow
indeterminacy.

The downloaded JSON set was aggregated twice byte-identically with
`scripts/aggregate_public_replay_github_evidence.py`. The permanent,
schema-version-1, source-free aggregate is
[`evidence/historical-public-replay-github-evidence-6c13c24.json`](../evidence/historical-public-replay-github-evidence-6c13c24.json),
828,210 bytes with SHA-256
`8122b4ee0a308ce1202f66e94c3cd6bf189c65641a6755f2de95ff1ec78127e2`.

## Classification result

| Classification | Requests | Accepted results | Meaning |
| --- | ---: | ---: | --- |
| `resolved` | 126 | 192 | Exact issue, accepted workflow/comment, and currently available public source matched; eligible for replay planning. |
| `source_unavailable` | 184 | 219 | Exact historical acceptance matched, but the public source revision is unavailable; remains pending. |
| `timing_indeterminate` | 2 | 2 | The otherwise matching public evidence was outside the conservative timestamp window; requires adjudication. |
| `evidence_missing` | 3 | 220 | No unique qualifying public evidence was found; remains pending. |
| **total** | **315** | **633** | 126 requests / 192 results resolved; 189 requests / 441 results remain pending. |

The accepted-result column is the runbook step-6 join of each classification
to the exact request artifact with SHA-256
`50202e7331a77ed04be04a784315b8ecfad6f593edc6686763d196552df5e2fa`.
CI regenerates that artifact from commit `6c13c245` and asserts both the digest
and every request/result classification count.

There are zero `source_probe_indeterminate`, `probe_indeterminate`,
`workflow_contract_unreviewed`, or `ambiguous` requests. Compared with the
first pass, all 57 permission-bound public Gist revisions are now resolved;
the other classifications are unchanged. Only the 126 resolved request groups
may advance to a newly generated exact-pin replay plan. The earlier 69-request
seed plan remains a valid conservative artifact but does not cover these newly
resolved Gists and must not be used as the complete public corpus plan.

## Shard provenance

The package digest is GitHub's digest of the artifact archive; the JSON digest
is the SHA-256 of the extracted evidence document. GitHub reports expiry on
2026-09-23. The checked-in aggregate and this table survive artifact expiry.

| Shard | Run | Artifact ID | Package SHA-256 | Evidence JSON SHA-256 |
| ---: | --- | ---: | --- | --- |
| 0 | [`32768996061`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32768996061) | `9535506192` | `4e9ae0df9750c0eb47214d6ff4cd9302046550ca242db60b16651bf839ee3007` | `9672f8161e27ee978f81f6b243c8c70025ea8ff66d4dd5f47d51b54df24a5e2d` |
| 1 | [`32769115520`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32769115520) | `9535549933` | `7d9b1770974ff28260ef0e49d75c8505d142e5d04307d734b7a491420c742e8e` | `fd8acf2b50b7e5385383a31192a1ccda4e28ea6ecc4d37838ca02eb179f7ab59` |
| 2 | [`32769239884`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32769239884) | `9535594273` | `d65bb23be088b00dd6274f4573a1863860b4c532b1a30b8a261643d22f908b26` | `b608f8329518d66664543fc7480c978bca9f5b4d6fdce59f9b309658d20077df` |
| 3 | [`32769431089`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32769431089) | `9535650814` | `c573d64fa9188cd1024358e15a79b103820e3615c4d76f3097cdabc7f663638c` | `24ccee716595478443bf4b5414c8388715674085108fab9dbffbf611e661a2b5` |
| 4 | [`32769521383`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32769521383) | `9535682311` | `113156aeef711cfb725a3442d6eda80e09535be551d522e98d097bc908d016df` | `3cf58571e0e59c7f46f824146726e2ed6bd3730415d537ac8e97996132cdad2a` |
| 5 | [`32769607989`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32769607989) | `9535726284` | `4aebbd9f52b030a48d332966acf2846f57f491488801c46b765b6effb0359c44` | `a7005aef38610f28029f2fc6d2f5d67a774ce676611d5bedfa534ee33121601f` |
| 6 | [`32769743219`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32769743219) | `9535773021` | `1b6f4891c8d1cedcf5d63ea204e7f306e88a509a662a9260272c7ecb86ee5d54` | `406fc2d669076aeba39207ad102331950f8b1d6715ee98ec399e8108c267d95e` |
| 7 | [`32769867509`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32769867509) | `9535804067` | `7a432d7ab43269398a2069cac54d304c4305af45dabcf47edf958c3094c97692` | `fac22299799ec9c74e069279e4aac7a6bc17cc47ae81d353f717e7f7a4255277` |
| 8 | [`32769959220`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32769959220) | `9535830029` | `45c676a787a019ecdeafa9d0baf611062375bfe83b393b4e7dda03a1927cfb33` | `39f6e39c27fcc3721ccc2fd6a2de6b08cbb7591b907666438f60a776c5761eac` |
| 9 | [`32770030710`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32770030710) | `9535854039` | `d5ab4b2fd4463fe4b1b72f9f70037b12c6caf5868d7f8fb9e56ad4412ee7142a` | `50d05eec88643eee629420cc45932a8f1a99f4cecedc506a217244ea8c0b9592` |
| 10 | [`32770102318`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32770102318) | `9535886562` | `01c209968873e7394e3836d8cb6a2d7d03d993cf0adbf572b2e72db12cd24f9b` | `d5686908639ff91d3fde7f8ea15967470ad4ba18279d081bfb8b84a25018a665` |
| 11 | [`32770204000`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32770204000) | `9535911803` | `c4c0d3a8cbc2af85271dbd945642c750d5cfd89af9ae97bd2f632e2b9417729f` | `6b42263547b4aa91856f6325e307a21101e8a01226756c136ed8b1b198db0525` |
| 12 | [`32770273676`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32770273676) | `9535942084` | `9d6ca3f49a24d44d175e9656b561da8c7c1df597ddb7ea75d974d553b6409c7e` | `6ca97adddb782d98eee82629f5972c02a1ee42669254750228a475c456743da1` |
| 13 | [`32770359205`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32770359205) | `9535971003` | `1cae4cefb20e07c76047a0db6d7c6888c93a6ae3307e9cbc15040e69ac009321` | `40237829f7ae4ef876d008d13c77ffb56b7c1350d9225649866d804d90fc1e7b` |
| 14 | [`32770445998`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32770445998) | `9536006300` | `1c5c5a9d0900ec204d29f524900c864b2829d80169a99e049db0812dc4df5e1a` | `72eb0f022637d2b7bbafc0b16371cb98446da5ee592de612ca443de56bfa0d43` |
| 15 | [`32770548866`](https://github.com/leanprover/lean-eval-submissions/actions/runs/32770548866) | `9536044511` | `c3fbeb5ab809df6c5f2a05e0105daef038cca53e43a37925792514b29011a050` | `17b1c3f84db2bc8651590956a9ad3a2d754a526ea67ff183434b29c503fd33ea` |

A later probe or adjudication must produce another immutable aggregate rather
than rewriting either retained evidence object.
