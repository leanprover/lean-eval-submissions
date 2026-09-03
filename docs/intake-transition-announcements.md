# Intake transition announcement templates

These templates prepare the D10 transition from GitHub-issue intake to the
lifecycle-aware server. Standing authorization is recorded in the completion
plan; these templates do not establish launch or closure readiness. Replace
every placeholder and link the supporting evidence before publishing an
announcement.

## Production server launch

Prepare these fields in the compact launch packet first:

```text
server URL: <URL>
enabled at: <UTC timestamp>
reviewed Worker commit: <40-character SHA>
deployment run: <GitHub Actions URL>
production health evidence: <URL or ledger row>
issue-intake target closure date: <YYYY-MM-DD>
incident contact: <maintainer or documented channel>
```

Ready-to-edit announcement:

> Lean Eval's lifecycle-aware submission server is now available at
> **<server URL>**. GitHub-issue intake remains available during an initially
> planned four-week transition, with a target closure date of **<date>**.
>
> Server-path submissions use authenticated, exact-ref intake and the approved
> publication policy. Evaluation-group source starts private; when the recorded
> publication choice is `scheduled`, accepted source is scheduled for release
> under the Apache License 2.0 exactly two UTC calendar months after acceptance.
> Choosing `scheduled` confirms that the submitter is authorized to grant that
> license. Choosing `withheld` keeps accepted source private; the submitter may
> later irreversibly change that choice to `scheduled`. Amendments, model-alias
> changes,
> publication choices, and lifecycle status are available through the server
> path. Issue-path
> submissions retain their existing policy during the transition.
>
> Please try the server path and report problems at **<incident/support URL>**.
> Issue intake will not close merely because the target date arrives: the
> incident, submitter-migration, adoption, and announcement gates below must
> also pass. Any revised closure date will be announced explicitly.

Publish the dated repository notice under standing authorization. Obtain exact
maintainer approval before posting the same notice in the LeanEval Zulip topic.
A Zulip copy is optional; it is not a launch, overlap, notice, or closure gate.
Keep the repository link and timestamp with the transition checklist.

## Issue-intake closure gate

Complete this evidence block before proposing closure:

```text
server enabled at: <UTC timestamp>
planned overlap end: <UTC timestamp>
selected closure date: <UTC timestamp>
unresolved severity-high server incidents: <zero or blocking links>
server-path share in final 14 days: <count>/<total> = <percent>
adequate-adoption assessment: <reviewed link and concise rationale>
stable archive/evaluation/State/release/leaderboard assessment: <reviewed link>
final historical cutoff and append-only delta: <commit and digest>
repository announcement published at: <UTC timestamp and link>
public closure notice published at: <UTC timestamp and link>
closure notice at least two weeks old: <yes/no>
standing authorization: <completion-plan section 11 link>
issue-retirement readiness packet: <reviewed link>
```

Closure is allowed only when all of the following are true:

1. at least four weeks have elapsed;
2. no severity-high server incident remains unresolved;
3. server intake, archive, evaluation, release scheduling, and leaderboard
   presentation are stable;
4. a maintainer has reviewed current server/issue submission counts and
   recorded that adoption is adequate to remove issue intake;
5. the final historical cutoff and append-only delta are recorded; and
6. a public repository closure notice has been available for at least two
   weeks.

Ready-to-edit closure announcement:

> Lean Eval GitHub-issue submission intake will close on **<date and UTC
> time>**. The lifecycle-aware server at **<server URL>** is the supported
> submission path. The transition gates and adoption evidence are recorded at
> **<ledger or reviewed PR URL>**. Existing issues and historical results are
> unaffected; this changes only how new submissions enter the system.

After the cutoff freeze, final delta, and retirement gates have passed, retire
issue intake through a reviewed pull request that replaces the submission issue
form with a server link. Do not delete historical issues, rewrite Results or
State, enable another feature, or shorten the published notice as part of that
change.

## Cutoff freeze mechanism

Keep the Issue Form present while the final corpus is reconciled. After the
readiness gates pass and before the selected cutoff arrives, set the
`lean-eval-submissions` repository Actions variable `ISSUE_INTAKE_CUTOFF` to
that future canonical UTC-second timestamp and read it back exactly. Do not wait
until the cutoff instant: installing the future timestamp in advance avoids a
repository-variable propagation gap. An absent variable means issue intake
remains open. The `Submission` workflow compares the variable with the first
attempt's immutable workflow-run creation time, or a rerun attempt's strict
start time, admits only attempts before the cutoff, and caches that decision
before intake. Server `workflow_dispatch` submissions do not pass through this
gate.

Before setting the variable, verify the selected future timestamp and that
every preceding closure gate except the final cutoff/delta readback is
satisfied. New issue runs at or after the cutoff must complete only the
admission job and perform no label, issue, source, archive, evaluation, Results,
State, or leaderboard mutation. First attempts created before the cutoff retain
their cached admission and may drain normally. A rerun is a new attempt: it is
admitted only when that attempt's `run_started_at` is also before the cutoff.

Before the selected cutoff, a wrong future value may be reversed by deleting
only `ISSUE_INTAKE_CUTOFF`, verifying it is absent, and leaving issue intake
open while a corrected cutoff is reviewed. Once the selected cutoff has passed,
deleting the variable reopens issue intake and is an incident-recovery action,
not ordinary rollback. Do not delete the variable merely to retry a post-cutoff
submission. Final retirement replaces the form with the server link only after
the final delta and readiness packet have passed.
