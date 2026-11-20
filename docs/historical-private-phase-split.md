# Private replay start and observation phases

The temporary private historical workflow uses the existing asynchronous
executor protocol in two jobs under the same noncancelling lane concurrency
lock. It does not activate replay or change the qualified image, official Lean,
nanoda, measurement limit, four-attempt cap, or blocked network policy.

`start-one` has a bounded 120-minute setup/admission window. It retains exact
protected source/State/Audit provenance, disposable resource ownership,
cleanup reservation, source-free prewarm, State CAS admission, ten-minute
one-use unwrap and reuse refusal. AWS authority is dropped before the
key-bearing executor request. Before every accepted State publication it
reserves 45 minutes of remaining setup, 790 seconds for the 780-second POST, kill grace and rounding, and a
12-minute cleanup tail. Every later private setup phase checks the absolute
monotonic clock; the final POST must begin within 45 minutes of the first prewarm and
still reserve its whole transport timeout and cleanup tail. Per-stage timeouts
bound stalls. Wall-clock timestamps bind remote receipt horizons; monotonic
clocks bound local job capacity against wall-clock corrections. No runtime allowance is shortened to make setup fit.

The only executor POST has an outer 780-second timeout with five-second forced
kill grace. After the response is exactly bound and HTTP 202, `accepted=true`
is emitted immediately, before further fallible work. This transfers cleanup
to `observe-one`; all source-job sandbox destruction, failure accounting and
resource deletion require accepted to be false. Host private-file/credential
scrubbing remains unconditional. An ambiguous POST stays with the source job's
existing destruction-confirmed failure path. Failed preparation or start stops
the lane; the initial rollout never automatically spends attempts across an
infrastructure outage.

The fresh six-hour `observe-one` job receives only a closed opaque binding:
workflow/run/first-attempt identity, lane, original State parent and start event,
plan/candidate/status hashes, nonce and qualified profile/measurement/image
identities. Archive locators, source, key material, capabilities and credentials
are never outputs or artifacts. The observer checks out immutable workflow
code, protected current metadata, State and encrypted Audit. It derives the
unique lane's running event and its actual Git addition parent from protected
State; re-materializes that exact parent; reconstructs the original canonical
plan/candidate/status; verifies every handoff hash and current running binding.
Unrelated main/State advancement is allowed; changed attempts/identity or
non-running tasks are refused. Both jobs refuse workflow reruns.

The observer never starts a process or unwraps; it polls the existing source-free
status route, validates the exact response, confirms destruction, appends the
truthful terminal outcome with CAS, and proves exact resource absence. Polling
ends at accepted-202 time plus the server's 335-minute process window plus
120 seconds for delivery of its timeout receipt. Queue time consumes this
remote window instead of restarting it. The polling step is capped by its own
job clock minus twelve minutes, rounded down, so even stalled State proofs
cannot consume the reserved tail. The existing seven-hour cleanup alarm and
stale recovery remain the fail-closed backstop after cancellation, missing
handoff or incomplete cleanup. No fabricated terminal/destruction events are
introduced. Replenishment requires both jobs successful, a reported terminal
outcome, private scrubbing in both jobs and exact resource deletion.

The pinned Sandbox SDK is 0.12.7. Its `getSandbox` applies options in the
background, so start POSTs omit idle configuration entirely. Only this
request's own successful new `startProcess` invokes an awaited private lifetime
hook. Existing-process repeats and process-ID collision losers skip it. If
setting the lifetime fails, the process is destroyed and no 202 is returned.
The finite initial idle duration is at most six hours and cannot pass the
original binding's claim time plus six hours (one hour before its fixed cleanup
alarm). The first observer status call explicitly restores fifteen minutes;
each subsequent poll maintains that policy. Prewarm and encrypted upload also
retain fifteen minutes. No indefinite keepalive or new heartbeat protocol is
used. The source job makes no status, prewarm or upload request after 202.

This initial idle policy accepts a bounded availability tradeoff: if the
observer is queued for hours or never arrives, the process/container may be
lost before a full 330-minute execution finishes. It does not reduce the
checker's immutable wall limit. A timely observer renews the normal short idle
policy; an abandoned one returns to the existing cleanup/recovery path. With
the 45-minute prewarm-to-POST bound and complete 13-minute POST, accepted 202
arrives within 58 minutes and ten seconds of the original claim. The 337-minute observation
horizon and 12-minute tail then end within 407 minutes and ten seconds, leaving over twelve
minutes before the unchanged seven-hour durable alarm.

Before a production pilot, independently review the exact source head, require
all CI, repair and verify the dedicated AWS alias invocation authority, rerun
all four canonical planners and private resource/no-running checks, and use
only canonical lane 2's first attempt with replenishment disabled. Source-free
timing metrics and exact unwrap/reuse/destruction proofs must pass before a
separately bounded campaign. Merely merging this implementation does not
satisfy those activation gates.
