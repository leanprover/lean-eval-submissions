# Original verification evidence

The trusted result-recording job writes `verification/<result_id>.json` alongside
each newly accepted result. These version-1 documents contain exactly
`schema_version`, `result_id`, `benchmark_commit`, `run_id`, and `run_attempt`.
Run IDs and attempt numbers are positive integers taken from GitHub Actions'
own environment, separately from submitter metadata and evaluation artifacts.

The original results records remain unchanged. Duplicate submissions never
replace their original evidence. Rejected submissions produce no evidence.
The documents include no private source or archive locators.

The website binds each document to the matching immutable result and benchmark
commit and constructs a link under the submissions repository's Actions URL.
Missing historical evidence remains explicitly unavailable. Maintainer-reviewed
backfills may add a document after checking the original run's source identity,
accepted result, and checker logs; they must not rewrite the original result.

The initial backfill covers the September 8 Yamaguchi submissions for Martinet
and Shafarevich, and the September 13 withdrawn Klartag launch canary. Their
original run IDs are 34192301049, 34192355619, and 34760378084 respectively.
