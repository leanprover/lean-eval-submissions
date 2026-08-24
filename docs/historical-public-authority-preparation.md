# Historical public replay authority preparation

The first historical image qualification and the first production State append
are separate review boundaries. A successful staging probe is evidence for an
execution profile; it is not permission to append State or enqueue replay.

`historical-public-authority-preparation.yml` implements the source-free bridge
between those boundaries. It consumes one exact successful version-2
qualification run and produces only review artifacts. The workflow has
`actions: read` and `contents: read`, no environment, no OIDC permission, and no
Cloudflare, AWS, registry, State, Results, or release credential.

## Exact inputs

Dispatch the workflow only from
`lean-eval-dispatch/<preparation-commit>`, where that exact commit is an
ancestor of protected `main`. The inputs name:

- the successful qualification run and attempt;
- the exact candidate and staging artifact IDs;
- the distinct qualification-controller and immutable-image source commits;
- one exact benchmark, seed-plan request, and result; and
- the explicit preparation-only confirmation.

The controller reads the run and artifact metadata through GitHub's read-only
API. It then consumes the two artifact ZIP archives themselves, verifies their
GitHub-reported SHA-256 values, and accepts exactly three candidate members and
one staging member. Duplicate names, extra files, links, directories,
encryption, traversal, oversize expansion, noncanonical JSON, or unsupported
compression fail closed. Locally supplied extracted files are never an input.

The version-2 evidence must keep replay disabled and bind all of the following
without normalization:

- controller source commit and original image source commit;
- benchmark commit/tree and matrix/profile-lock digests;
- immutable registry tag and manifest digest;
- healthy `standard-4`, 12 GiB, 20 GB, SSH-disabled, private/no-address rollout;
- two distinct successful probes with one nonce, blocked networking, confirmed
  destruction, and identical architecture, kernel, and CPU model; and
- the exact Dockerfile and optional layer-preparation helper from Git objects
  at the image source commit.

The plan, profile matrix, runner contract, qualification contract, qualification
workflow, and qualification controller are also read from exact Git commit
objects. Worktree bytes, including hidden `skip-worktree` changes, are not
authority.

## Output and remaining blockers

The output contains a qualified execution-profile object at
`evidence/public-replay/profiles/<execution-profile-digest>.json` and a blocked
append preparation. Profile and measurement digests use the ordinary replay
orchestrator's domain-separated compact canonical JSON algorithm. The append
preparation contains the exact historical authorization payload, the profile
qualification payload except for its not-yet-known commit, and the ordinary
`replay.enqueued` payload/task identity.

It remains explicitly blocked on all five steps recorded in the artifact:

1. Review and commit the byte-identical profile at its digest-derived path.
2. Supply the exact commit containing that blob.
3. Supply fresh, strictly increasing State event IDs and occurrence times.
4. Validate the three-event append against current production State.
5. Separately authorize the State append and replay enqueue.

The offline `finalize` command enforces those conditions mechanically. It
requires clean exact checkouts of the qualification commit and pinned
production State `501d237d46c7b3466a37554c1c2ceb310245a619`. It proves the
qualification blob with `git show`, reconstructs the State event and script
inputs from exact commit objects, validates the authority → qualification →
enqueue chain with the pinned State validator, and requires the materializer to
emit exactly one queued historical-public task. UUIDv7 embedded milliseconds
must equal `occurred_at`, and the candidate must follow the pinned State time
window. Even then the output remains a local append candidate: the command does
not commit, push, append, enqueue, deploy, or enable anything.

The finalized command is intentionally not called by the preparation workflow.
It becomes usable only after the profile-review PR lands:

```console
python scripts/prepare_historical_public_authority.py finalize \
  --preparation /path/historical-public-authority-preparation.json \
  --profile /path/evidence/public-replay/profiles/<digest>.json \
  --qualification-commit <exact-commit> \
  --qualification-repository-root /clean/exact/submissions-checkout \
  --state-root /clean/exact/state-501d-checkout \
  --authority-event-id <uuid7> \
  --authority-occurred-at <utc-milliseconds> \
  --qualification-event-id <uuid7> \
  --qualification-occurred-at <utc-milliseconds> \
  --enqueue-event-id <uuid7> \
  --enqueue-occurred-at <utc-milliseconds> \
  --output-directory /new/review-directory
```

The qualification and State roots must set `origin` to exactly
`https://github.com/leanprover/lean-eval-submissions.git` and
`https://github.com/leanprover/lean-eval-state.git`, respectively. SSH remotes
and HTTPS remotes without the `.git` suffix are rejected.

The first workflow defaults select benchmark `11081d34…`, request
`prr_9927609e…`, and result `r2_70b509d7…`. Those defaults are a deterministic
first candidate, not an authorization. Until an exact successful qualification
artifact exists and the later review/append steps complete, production State
and the historical queue remain unchanged.
