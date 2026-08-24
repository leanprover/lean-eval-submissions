# Historical public replay resolution

The historical inventory intentionally stops before it guesses which GitHub
repository received an old issue submission or which workflow revision ran it.
Both `leanprover/lean-eval` and `leanprover/lean-eval-submissions` have used the
same issue numbers at different times, so a date cutoff or issue number alone
is not authoritative.

`scripts/prepare_public_replay_resolution.py` is the deterministic next stage.
It requires an inventory that byte-for-byte describes the exact recomputed
schema-version-2 results store. For every public result it restores the omitted
issue number, canonical owner, and declared model from that result, cross-checks the complete
source/benchmark/result identity, and groups all problems from one submission.
Each request retains both possible issue repositories and the exact expected
`issues` / `Submission` workflow identity. It does not select a repository,
workflow run, evaluator commit, toolchain, or replay verdict.

At the current reviewed store digest this produces 315 evidence requests for
633 public results: 136 GitHub-repository submissions and 179 public gists. One
submission contains 138 accepted problem results, so evidence resolution and
replay execution must remain submission-grouped rather than charging setup once
per result.

The manual `public-replay-github-evidence.yml` workflow is that
external-credential-free stage. It can run only from an immutable dispatch tag
whose lightweight target or annotated-tag peeled target is the exact reviewed
commit, which must also be reachable from live `main`; the branch API must report
that `main` is protected and at the same commit seen through Git. It recomputes
both the inventory and request set before using its read-only GitHub token. For
each candidate it requires the issue model and normalized source identity,
successful `issues` /
`Submission` run, issue author/title/timing, a bot result comment no more than
ten seconds after the immutable accepted-result timestamp, with every expected
problem ID explicitly reported as passing. The comment may contain additional
passes whose already-existing result identities were retained during result
store deduplication. A full commit pinned in the issue URL must also
equal the recorded source commit.

Before the workflow split, the run head is the exact benchmark commit and the
resolver hashes the complete workflow definition at that commit. After
the split, the run head instead names the evaluator revision in
`lean-eval-submissions`; the resolver separately pins that commit and the exact
workflow-definition digest. It accepts the split contract only when that exact
commit/digest pair appears in the reviewed
`configuration/public-replay-workflow-definitions-v1.json` registry. The reviewed
definition must resolve `leanprover/lean-eval@main` and propagate that SHA
unchanged into the accepted record. The resolver does not incorrectly equate
those two repository commits.

The registry is closed by default: an unknown evaluator commit produces
`workflow_contract_unreviewed` and remains pending; text fragments, comments,
or dead steps never establish a contract. The first historical review covers
119 exact commit/definition tuples and is documented in
[`historical-public-workflow-contract-review.md`](historical-public-workflow-contract-review.md).
To review another entry, start from that pending candidate's exact commit and
definition digest, prove the commit is reachable from protected
`lean-eval-submissions` `main`, retrieve `.github/workflows/submission.yml` at
that commit, verify the complete checkout, SHA-output, job-output, and
record-input data flow, and recompute the exact blob SHA-256. Add the sorted
tuple through a reviewed PR and rerun the affected shard. Never add a digest
based only on a fragment search or the previous resolver's classification.

Finally, the resolver explicitly verifies that both historical issue
repositories and the selected source repository are public and readable, then
verifies exact commit/revision availability. Repository probes use metadata-only
Git commit endpoints.
GitHub has no content-free REST metadata endpoint for a gist revision, so a
bounded public gist response is parsed transiently; no source field is
persisted, logged, uploaded, or cached. An oversized response, refused redirect,
repository rename, HTTP 451, permission/rate boundary, or exhausted request
retry becomes a reason-coded indeterminate pending probe for only that request;
it neither aborts the shard nor proves source unavailability.

The uploaded artifact is a closed, sanitized projection of URLs, commits,
identifiers, classifications, and counts. It does not contain issue bodies,
source files, workflow logs, or private locators. It retains bounded SHA-256
identities for the issue title/body, normalized issue identity, selected run,
and result-comment body, plus a separate source-ref digest, exact public
timestamps, run attempt, canonical owner, and pass-ID projection needed to
re-audit a match. The issue author must
case-insensitively equal the canonical result owner. Exactly one matching candidate
with an available source becomes `resolved`. An exact match whose source has
disappeared becomes `source_unavailable`, but remains pending rather than being
turned into a permanent-unavailability verdict. Zero matches and multiple
matches likewise remain pending. A matching workflow run or bot comment just
outside the conservative lag window is retained with public timestamps and
digests as `timing_indeterminate` for adjudication. An exact but unreviewed
split-workflow tuple is separately `workflow_contract_unreviewed`. The workflow
never guesses between the two historical issue repositories.

The resolver accepts only the fixed `api.github.com` origin and rejects
redirects, preventing its token from being forwarded to an alternate host.
Inputs and API responses are bounded before use, request IDs are recomputed
from their closed submission identity, and every output URL, workflow contract,
selected repository, classification, shard member, result total, and status
counter is checked against the reviewed request set. Output creation is
exclusive and does not follow a pre-existing final-path symlink.

`pending_count` is exactly the number of non-`resolved` entries already present
in `resolutions`; there is intentionally no second pending array. In particular,
the count includes `source_unavailable`, `source_probe_indeterminate`,
`probe_indeterminate`, `timing_indeterminate`,
`workflow_contract_unreviewed`, `ambiguous`, and `evidence_missing` entries.

Resolution is deterministically partitioned into balanced contiguous ranges of
the requests sorted by acceptance time and request ID. This keeps daily Actions
run-list probes local to one shard instead of repeating most dates in every
hash partition. One manual
workflow run processes exactly one reviewed shard and uploads an artifact bound
to its shard index, total shard count, request count, and result count. Shards
must be scheduled across GitHub rate-limit windows; they are deliberately not a
parallel matrix that would share and exhaust the repository's standard
`GITHUB_TOKEN` budget. Empty date-local partitions are producible zero-count shards
and must still be supplied to aggregation. The resolver uses entry-bounded LRU caches
for repeated workflow definitions and daily run lists, but never gist bodies. Its token has
only repository contents, issues, and Actions read authority; source fetching,
State/Results writes, deployments, and secrets are outside that boundary.

Public Gist probes omit that repository token and make one anonymous attempt.
Each shard is rejected before probing unless it contains at most 20 Gist
requests, bounding the two historical issue candidates to at most 40 anonymous
API calls and retaining headroom below GitHub's anonymous hourly limit. Choose a
larger shard count if this preflight fails; do not run a partial over-budget
shard.

After every shard for one reviewed `shard_count` has completed, download the
sanitized artifacts and aggregate them offline from the same clean protected
commit:

```bash
python scripts/aggregate_public_replay_github_evidence.py \
  --requests /reviewed/public-replay-resolution-requests.json \
  --workflow-registry configuration/public-replay-workflow-definitions-v1.json \
  --evidence /reviewed/shard-0.json \
  --evidence /reviewed/shard-1.json \
  --output /reviewed/public-replay-github-evidence-aggregate.json
```

Supply every shard exactly once. The aggregator binds one source commit,
inventory digest, request byte digest, exact registry-file byte digest, and shard count;
requires every index and request ID exactly once; revalidates every candidate;
recomputes result coverage and counters; and records every shard SHA-256. Its
schema is `schemas/public-replay-github-evidence-aggregate-v1.schema.json`.
Aggregation does not mutate State or promote a classification:
`source_unavailable`, all indeterminate classes, unreviewed workflow contracts,
ambiguous matches, and missing evidence remain pending with separate counters.

This evidence can recover the exact evaluator commit from the workflow run and
bind the accepted public result to its issue. When an old unpinned source URL
was used, the immutable accepted result remains the record of the fetched
source commit because the expired Actions artifact cannot be recreated. Corpus
replay must preserve that distinction instead of claiming the current probe is
new proof of what the historical fetch observed.

Only resolved requests may advance to deterministic shards that restore the
exact evaluator revision, benchmark toolchain, and public source ref for corpus
replay.

The first complete post-registry pass is durably recorded in
[`historical-public-evidence-rerun.md`](historical-public-evidence-rerun.md).
Its reviewed aggregate is committed in `evidence/` so the classification audit
does not disappear when the 30-day Actions shard artifacts expire. A later
probe or adjudication creates a new aggregate; it never rewrites this evidence
object.

That deterministic, source-free bridge is specified in
[`historical-public-replay-plan.md`](historical-public-replay-plan.md). Its seed
plan remains explicitly blocked until State has a reviewed replay-authority
contract for legacy public Results records; it never invents a modern
submission ID or archive receipt.

Exact model mismatches remain pending for explicit adjudication. For example,
an issue description that adds a context-window qualifier to the accepted
record's model name is not silently normalized into a match.
