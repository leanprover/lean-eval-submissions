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

After that announcement and its stated date, close issue intake through a
reviewed pull request that replaces the submission issue form with a server
link. Do not delete historical issues, rewrite Results or State, enable another
feature, or shorten the published notice as part of that change.
