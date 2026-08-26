# Intake transition announcement templates

These templates prepare the approved D10 transition from GitHub-issue intake
to the lifecycle-aware server. They are not authorization to enable or close
either path. Replace every placeholder and link the supporting evidence before
publishing an announcement.

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
> license; choosing `withheld` opts out. Amendments, model-alias changes,
> publication choices, and lifecycle status are available through the server
> path. Issue-path
> submissions retain their existing policy during the transition.
>
> Please try the server path and report problems at **<incident/support URL>**.
> Issue intake will not close merely because the target date arrives: the
> incident, submitter-migration, adoption, and announcement gates below must
> also pass. Any revised closure date will be announced explicitly.

Publish the same dated notice in repository documentation and the LeanEval
Zulip topic. Keep both links and the timestamp with the transition checklist.

## Issue-intake closure gate

Complete this evidence block before proposing closure:

```text
server enabled at: <UTC timestamp>
planned overlap end: <UTC timestamp>
selected closure date: <UTC timestamp>
shorter-than-four-weeks decision and prior announcement: <link or N/A>
unresolved severity-high server incidents: <zero or blocking links>
five most active submitters in preceding 60 days: <private operator record>
their server submission or no-dependency confirmation: <5/5 evidence summary>
server-path share in final 14 days: <count>/<total> = <percent>
fewer than ten submissions, if applicable: <manual assessment link or N/A>
repository announcement published at: <UTC timestamp and link>
Zulip announcement published at: <UTC timestamp and link>
both announcements at least two weeks old: <yes/no>
maintainer approval to close: <link>
```

Closure is allowed only when all of the following are true:

1. four weeks have elapsed, unless a maintainer selected and announced a
   shorter overlap before closure;
2. no severity-high server incident remains unresolved;
3. each of the five most active submitters from the preceding 60 days has made
   a server-path submission or explicitly confirmed no migration dependency;
4. at least 90% of submissions in the final 14 days used the server when that
   window contains at least ten submissions; otherwise a maintainer has
   recorded a manual adoption assessment; and
5. the transition has been announced on Zulip and in repository documentation
   for at least two weeks.

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
