# Automatic larger-runner retries

Every submission starts on Namespace with 16 CPUs and 32 GB RAM. A resource
failure during evaluation can trigger **one** fresh attempt with 32 CPUs and
64 GB RAM. Both attempts use the same archived source identity, benchmark
commit, toolchain, sandbox, and six-hour job budget. The final attempt alone
supplies the State completion and leaderboard result. Both attempts retain
their source-free diagnostics for seven days.

Issue submissions using branch URLs or unpinned gists are refetched at the
archived commit, so a later push cannot change or invalidate the larger attempt.
Completed standard evaluations bypass escalation and do not depend on policy
lookups or notification delivery.

Evaluation has a controlled deadline two minutes before GitHub's hard job
timeout. This leaves time to upload the resource report and turns an elapsed
time limit into a normal retryable failure instead of a workflow cancellation.

The larger attempt requires the authenticated submitter's GitHub username in
[`configuration/large-runner-allowlist.json`](../configuration/large-runner-allowlist.json).
The initial list is empty. Add a username as a JSON string in that array and
merge to protected `main`; the next retry decision reads that policy directly,
without requiring a Worker redeployment. Matching is case insensitive and uses
the issue author or the Worker-verified `submitted_by`, never the source repo
owner, issue labeler, or a field in submitted Lean code.

Users outside the list still receive normal evaluation. If more resources are
needed, an issue comment or the submission status API tells them:

> Your submission appears to need a larger runner. Please ask Kim Morrison
> (@kim-em) to add your GitHub username to the larger-runner allowlist, then
> resubmit. This is not a rejection of your proof.

That outcome is an evaluation failure (`large_runner_allowlist_required`),
not a rejected proof. It never advances the leaderboard. After approval the
submitter resubmits; adding a username does not resurrect completed runs.

Retry signals include an increased cgroup OOM-kill count, allocation-failure
diagnostics, unexpected process/runner kills, and an evaluation job timeout.
A separate trusted GitHub runner makes the decision, so losing the first
machine does not require an artifact to survive. It uses the resource report
and GitHub job-step status, without downloading or parsing job logs. A fully
passing evaluation takes precedence over resource-looking diagnostic text.
Ordinary Lean errors,
negative proof verdicts, heartbeat limits, user cancellation, and setup or
download failures do not trigger escalation. A signal exit is evidence for
trying more resources, not proof that memory was exhausted.

The 32 CPU / 64 GB tier has been provisioned successfully in this workspace.
The runner uses the same privileged feature required for comparator's
bubblewrap sandbox. A failed larger attempt ends evaluation;
there is no additional escalation tier or automatic retry loop.
