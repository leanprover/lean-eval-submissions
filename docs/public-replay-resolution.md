# Historical public replay resolution

The historical inventory intentionally stops before it guesses which GitHub
repository received an old issue submission or which workflow revision ran it.
Both `leanprover/lean-eval` and `leanprover/lean-eval-submissions` have used the
same issue numbers at different times, so a date cutoff or issue number alone
is not authoritative.

`scripts/prepare_public_replay_resolution.py` is the deterministic next stage.
It requires an inventory that byte-for-byte describes the exact recomputed
schema-version-2 results store. For every public result it restores the omitted
issue number and declared model from that result, cross-checks the complete
source/benchmark/result identity, and groups all problems from one submission.
Each request retains both possible issue repositories and the exact expected
`issues` / `Submission` workflow identity. It does not select a repository,
workflow run, evaluator commit, toolchain, or replay verdict.

At the current reviewed store digest this produces 315 evidence requests for
633 public results: 136 GitHub-repository submissions and 179 public gists. One
submission contains 138 accepted problem results, so evidence resolution and
replay execution must remain submission-grouped rather than charging setup once
per result.

The next credential-free workflow must fetch both candidate issues and their
successful Actions runs, then accept exactly one candidate whose source/model,
event, workflow name, timing, benchmark revision, and recorded results agree.
Ambiguous, missing, or inconsistent evidence remains pending; it is not a
permanent-unavailability verdict. Only after that resolution may deterministic
shards restore the exact evaluator commit, benchmark toolchain, and public
source ref for corpus replay.
