# Production launch readiness packet

Status: **NO-GO — final bounded lifecycle smoke and all-false recovery not
yet bound**

Scope authority: [LeanEval completion plan, section 7.5][completion-plan]

This is the compact production go/no-go packet. It records only the current
launch contract and the exact facts a maintainer must review before enabling a
capability. Update values in place; do not add a run history or evidence
appendix.

The lifecycle candidate follows the qualified all-false State-contract repair.
The rollback unit is live and coherent. The lifecycle candidate is protected
and immutable-tagged. Its final bounded lifecycle exercise and all-false
recovery are still in progress, and a fresh exact-candidate deployment must
reach a held production job after that recovery. Bind those terminal results
and re-read the mutable heads and gates below before changing this packet to
`GO`. Nothing in this document currently authorizes production enablement.

[completion-plan]: https://github.com/leanprover/lean-eval/blob/main/docs/overhaul-completion-plan.md#75-production-launch-readiness-packet

## 1. Exact binding

The table distinguishes immutable runtime evidence from mutable repository
heads. It was read at `2026-09-01T17:34:47Z`; the staging State value is an
in-progress smoke value and is not the required final recovered head.

| Repository or branch | Protected head at packet preparation | Runtime or data evidence bound by this packet |
| --- | --- | --- |
| `leanprover/lean-eval` `main` | `bcc165c27c6c546b27408454af35e3533e966463` | Authoritative completion plan and execution runbook |
| `leanprover/lean-eval-submissions` `main` | `38bd445d2242e71d1d09a304c1c1e78d987895a0` | Lifecycle runtime candidate and immutable dispatch tag |
| production submission Workers | not a Git branch | Qualified all-false rollback runtime `451856ebdd4ca4d875e43be7cd113678dea9e1b7` |
| production Results (`lean-eval-submissions/main`) | `38bd445d2242e71d1d09a304c1c1e78d987895a0` | `results/` tree `c1dd365238544209c8364d7d57ace275e0a20971` |
| staging Results (`lean-eval-submissions/staging-results`) | `06bfd1ed3f7a11db5cb33f5a581330077e55e80e` | `results/` tree `246fed6b9ac40f1ab5476374510a45aee741b573` |
| `leanprover/lean-eval-leaderboard` `main` | `b6df2533e2a6ceea8a6ed6eff5527cc3aef3e7c2` | Same commit, Pages deployment `6201347757` |
| `leanprover/lean-eval-state` `main` | `9cf3b4999bae2b6faaa32ff1bf5f040c5e6f787f` | Production State and release-queue source |
| `leanprover/lean-eval-state-staging` `main` | `bba17baad28c61b03311f20080f0ffbad4f74656` | In-progress smoke head; replace with the final all-false recovered head |
| `leanprover/lean-eval-releases` `main` | `3c68d99f3de7060f7f0fdacf9340354775546c05` | Release-controller and credential-boundary evidence |
| `leanprover/lean-eval-audit` `main` | `f50c46574dd719486a01272e3eaeced396ac5ada` | Audit-read evidence target |
| `leanprover/lean-eval-generator` `main` | `010b01634cccda2db538cf9b09e6f26ddc453743` | Generator baseline |

The protected merge of this packet may be a descendant of runtime candidate
`38bd445d2242e71d1d09a304c1c1e78d987895a0` without another Worker deployment
only if its complete diff from that candidate is restricted to `docs/**`, its
protected checks pass, and its `results/` tree is still exactly
`c1dd365238544209c8364d7d57ace275e0a20971`. Record that protected packet merge
SHA in section 9. The runtime candidate, immutable dispatch tag, staged version
IDs, and runtime evidence remain bound to `38bd445d2242e71d1d09a304c1c1e78d987895a0`;
never relabel them as evidence for the documentation descendant.

Production remains fail-closed at qualified replacement baseline
`451856ebdd4ca4d875e43be7cd113678dea9e1b7`. The intake candidate is draft PR
`#1600` at exact head `0c80159bd060167694db8200fc2f115893775482`, based
directly on lifecycle runtime candidate
`38bd445d2242e71d1d09a304c1c1e78d987895a0`. Its exact seven-file scope is:

- `docs/intake-threat-model.md`;
- `server/README.md`;
- `server/worker-configuration.d.ts`;
- `server/wrangler.jsonc`;
- `tests/test_monitor_cloudflare_health.py`;
- `tests/test_worker_deployment_workflow.py`; and
- `tests/test_worker_intake_configuration.py`.

The only runtime change is production `INTAKE_ENABLED: false -> true` and
`INTAKE_ENABLEMENT_MODE: disabled -> durable`; staging, the six lifecycle
gates, both maintainer arrays, model consolidation, publication opt-out,
promotion canary, and replay retain the lifecycle candidate's exact values.
The other six files are the matching generated type, current contract text,
and focused expectations. Any head, base, path-set, or semantic drift requires
fresh review before launch.

## 2. Capability decision

The launch sequence consists of separately visible actions. Capability gates
have explicit pause or rollback paths; the canary's publication opt-in is
irreversible, and publishing the announcement starts the overlap. Standing
maintainer authorization covers the sequence after every gate in this packet
reads `GO`.

| Capability | Current production state | Launch state |
| --- | --- | --- |
| Automatic release controller | `PUBLICATION_ENABLED` absent | `true`, initially with an empty release queue |
| Result-owner APIs | disabled | legacy-result claim prerequisite, metadata backfill, and repair/retraction enabled |
| Maintainer APIs | disabled, empty lists | decisions enabled for exactly `kim-em` / GitHub user `477956` |
| Model identity APIs | identity-creation prerequisite and alias/rename disabled; consolidation disabled | identity creation and alias/rename enabled; consolidation remains disabled |
| Publication opt-in | disabled | one-way private-to-scheduled transition enabled; reverse transition disabled |
| Server intake | disabled, durable lease absent | enabled through the finite-lease controller, then durable |
| Production canary | none | one packet-bound, withheld-source canary after intake; verify archive, evaluation, State, Result, leaderboard, and scheduling |
| General and historical replay | disabled | remain disabled; bounded historical workflows are separate |

The four production actions are not bundled:

1. after this packet is protected and reads `GO`, set and read back the release
   controller latch while the exact queue remains empty;
2. only then approve the already-held production job from the fresh exact
   `38bd445d2242e71d1d09a304c1c1e78d987895a0` deployment run and read back the
   lifecycle-only state below;
3. merge exact draft intake candidate
   `0c80159bd060167694db8200fc2f115893775482`, complete its finite-lease proof,
   and verify the transition to tracked durable intake; and
4. run the packet-bound production canary from private acceptance through
   archive, evaluation, Result, State, leaderboard visibility, and visible
   irreversible publication opt-in.

Publish the repository announcement only after all four actions succeed. It is
not a fifth capability mutation; it starts the issue-intake overlap. Before the
packet itself is merged, start a fresh protected deployment of exact runtime
candidate `38bd445d2242e71d1d09a304c1c1e78d987895a0`, require its check, tag,
staging deployment, and promotion canary to succeed, and leave its one
production job waiting in `cloudflare-production`. A completed, cancelled, or
superseded production job is not an acceptable hold.

The exact lifecycle configuration and readback contract is:

| Field | Tracked lifecycle candidate | Live rollback baseline | Required after action 2 |
| --- | --- | --- | --- |
| `LEGACY_RESULT_OWNER_API_ENABLED` | `true` | `false` | `true` |
| `RESULT_AMENDMENT_OWNER_API_ENABLED` | `true` | `false` | `true` |
| `RESULT_AMENDMENT_MAINTAINER_API_ENABLED` | `true` | `false` | `true` |
| `MODEL_IDENTITY_OWNER_API_ENABLED` | `true` | `false` | `true` |
| `MODEL_IDENTITY_MAINTAINER_API_ENABLED` | `true` | `false` | `true` |
| `RELEASE_OPT_IN_API_ENABLED` | `true` | `false` | `true` |
| result-amendment maintainer array | `[{"github_id":477956,"login":"kim-em"}]` | `[]` | exact tracked singleton |
| model-identity maintainer array | `[{"github_id":477956,"login":"kim-em"}]` | `[]` | exact tracked singleton |
| intake / intake mode | `false` / `disabled` | `false` / `disabled` | `false` / `disabled` |
| model consolidation / publication opt-out / promotion canary | all `false` | all `false` | all `false` |
| general replay / historical-public replay / production acceptance | all `false` | all `false` | all `false` |

The deployment job must verify the nonpublic maintainer arrays from its exact
checked-out configuration. Public `/healthz` and authenticated `/readyz` must
independently report the six booleans and all false gates, but intentionally do
not disclose the arrays. Final exact config-parser, deployment-job, health, and
readiness readbacks belong in section 9. Staging must return to intake and all
six lifecycle gates false with both maintainer arrays empty before the fresh
held run is accepted.

The enabled surface includes two prerequisite routes rather than hiding them
behind feature labels: `POST /api/v1/results/claims` establishes immutable
owner authority before historical metadata backfill, and `POST
/api/v1/model-identities` creates the owner-bound identity required before an
alias or rename. They share the reviewed owner gates and do not enable intake,
model consolidation, replay, or publication.

## 3. Gates from completion-plan sections 7.2–7.4

| Gate | Current result |
| --- | --- |
| Disabled baseline | Qualified at live commit `451856ebdd4ca4d875e43be7cd113678dea9e1b7`: production intake, general replay, historical-public replay, every public lifecycle family, model consolidation, and the promotion canary are effectively disabled. Its intake, broker, and replay version IDs are bound in section 9, and disable-only recovery run `33535723990` passed against the same commit. Protected production State is `9cf3b4999bae2b6faaa32ff1bf5f040c5e6f787f`; its validated 489-event materialization has release-queue source digest `d3392218297ea11f6093b59e5252f3ae394887368e02cea40a58e6fd82a901b` and zero tasks. |
| Archive boundary | Schema-version-3 archive-before-evaluation is deployed. Production archive authority is connected to the Encrypt-only Wrap role and has a qualified decrypt denial. The evaluation lane has no Wrap or unwrap authority. |
| Staging release boundary | Qualified: exact one-submission scope, consume-before-unwrap, identical reuse refusal, authority removal before reconstruction, source allowlisting, no plaintext artifact, no State/Git mutation, and cleanup. Publication and production authority remained absent. |
| Production release preflight | Qualified at release commit `3c68d99f3de7060f7f0fdacf9340354775546c05` and audit commit `f50c46574dd719486a01272e3eaeced396ac5ada`: controller credentials, audit read, and exact ID-bearing `release-production` OIDC preflights pass. The environment currently contains exactly the three named deploy-key secrets and the scoped AWS unwrap-role variable listed in section 9; `PUBLICATION_ENABLED` is absent. Empty-queue run `33525394698` selected no work and skipped unwrap/publication. No production archive may be decrypted or published while the packet is `NO-GO`. |
| Entry, OAuth, and submitter UI | The static `https://lean-lang.org/eval/submit/` entry page and stable production application at `https://lean-eval-submission-server.lean-eval.workers.dev/` are live in disabled posture. The entry page states the policy and links to the Worker origin; the Worker supplies OAuth feedback, preserved form values, progress spinners, and status. Production OAuth remains bound to client `Ov23liFcOLHsyvY9DmQ5`, its exact Worker callback, `read:user`, disabled device flow, and expiring user tokens. One-way publication opt-in is kept separate from new intake on `/release/` when enabled. No LeanEval hostname or DNS change is required. |
| Leaderboard | Protected main `b6df2533e2a6ceea8a6ed6eff5527cc3aef3e7c2` has successful Pages deployment `6201347757`. The live root, problem index, category page, and representative problem page return `200`; the problem page visibly contains its statement. Site-data schema 4 exposes 301 problems and representative per-problem JSON includes solution metadata. |
| Exact-version staging | The replacement all-false baseline has protected CI, exact-version staging and production readback, a promotion canary, and a successful no-op recovery proof. Lifecycle candidate `38bd445d2242e71d1d09a304c1c1e78d987895a0` has successful exact-head CI and an immutable tag. Its bounded lifecycle smoke is not yet terminal. After its all-false recovery, require one fresh exact-`38bd` deployment whose check, staging deployment/readback, and promotion canary pass and whose production job remains waiting. Bind the post-recovery staging versions, final State/Results heads, and held job in section 9 before `GO`. |

Any non-coherent Worker deployment, nonempty due-release queue, failed final
staging case, or unexpected State event changes this packet to `NO-GO`.

## 4. Credentials and ownership

The exact identifiers, environments, dates, and procedures are maintained in
[`INFRASTRUCTURE.md`](../INFRASTRUCTURE.md). Kim Morrison is the temporary
custodian except where organization ownership provides recovery.

| Credential class | Scope | Rotation and revocation |
| --- | --- | --- |
| Production Cloudflare deploy token | Dedicated `lean-eval` account; Workers Scripts and Containers edit only; no zone/DNS permission | Created `2026-08-21`, no expiry. Replace only in `cloudflare-production`, verify, then revoke the old token. |
| Worker `READINESS_TOKEN` and `LIFECYCLE_CALLBACK_TOKEN` | Matching production Worker endpoint and protected GitHub environment only | No application expiry. Replace each Worker/environment pair as one unit; overwrite both copies to revoke. |
| Worker `AUTH_TOKEN_SECRET` and OAuth client secret | Production session signing and the production personal OAuth App only | Replace only the production Worker value and verify fresh sessions/OAuth before revoking the old value. An auth-secret change intentionally invalidates existing sessions. |
| Production State writer | Contents write on `leanprover/lean-eval-state` only | Expires `2026-11-19`; rotate by `2026-11-05`. Install and verify an equally scoped replacement, then revoke the old token. |
| Source-reader and workflow-dispatch App keys | Metadata/contents read on opted-in source repos; metadata/contents/actions on submissions only | No recorded expiry. Create one replacement App key, install and verify it, then delete the old key. App ownership by `leanprover` is the recovery path. Neither App has Gist or broad organization authority. |
| Release deploy keys | Audit read, production State write, and releases write are three distinct keys | No provider expiry. Replace and verify one key at a time, then delete its old public key. Immediate revocation deletes the public key first and removes its environment secret. |
| AWS archive and release sessions | GitHub OIDC only: production archive may assume the Encrypt-only Wrap role; production release may invoke only the versioned unwrap Lambda | No long-lived AWS access key. Revoke by removing the exact environment role variable or narrowing/removing its OIDC trust. Release pause also removes the publication latch and cancels active runs. |

Production archive authority is connected and its decrypt denial is qualified.
Production release authority is connected to the exact ID-bearing OIDC subject
and its publication-disabled preflights pass. Automatic publication remains
disabled because the separate publication latch is absent.

At `2026-09-01T17:34:47Z`, the `release-production` environment exposed no
`PUBLICATION_ENABLED` variable. Its only repository variable was
`AWS_RELEASE_UNWRAP_ROLE_ARN`, set to
`arn:aws:iam::161072922960:role/lean-eval-release-unwrap-invoker-production`,
and its exact secret-name inventory was `AUDIT_READ_KEY`,
`PRODUCTION_STATE_CONTROLLER_KEY`, and `RELEASE_PUBLISH_KEY`. The
`cloudflare-production` environment's exact secret-name inventory was
`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`,
`LIFECYCLE_CALLBACK_TOKEN`, and `READINESS_TOKEN`. GitHub does not expose the
secret values or prove which runtime Worker secret version is installed; the
bounded deployment, readiness, callback, and OAuth evidence supplies that
operational proof without recording a secret.

Browser OAuth is temporarily owned by personal account `kim-em`, which is an
accepted launch limitation. Production client ID
`Ov23liFcOLHsyvY9DmQ5` has the exact callback
`https://lean-eval-submission-server.lean-eval.workers.dev/api/v1/oauth/callback`,
requests only `read:user`, disables device flow, and uses expiring user tokens.
Kim Morrison owns rotation and account recovery; if that account is
unavailable, pause browser intake. Later transfer to `leanprover` is not a
launch gate.

The current public GitHub App metadata has no subscribed events and matches
the following closed permission contract. GitHub does not expose installation
selections through the current operator token, so the final private-access
checks are explicit packet slots rather than inferred claims.

| App | Owner / App ID | Exact permissions | Required final access readback |
| --- | --- | --- | --- |
| `lean-eval-source-reader` | `leanprover` / `4666604` | Metadata read, Contents read | Production broker preflight reads private `leanprover/lean-eval-state-staging`; `<successful URL>` |
| `lean-eval-workflow-dispatcher` | `leanprover` / `4666633` | Metadata read, Contents read, Actions write | Installation `155329316` selects exactly `leanprover/lean-eval-submissions`; fresh held deployment dispatch succeeds; `<compact readback>` |
| `lean-eval-bot` | `kim-em` / `3346375` | Metadata read, Contents read | Exact canary branch `production-canary-source-fixture-v4` at `18595c8d06ec29d47940065644d16ae7cfbd1591`; `<successful preflight URL>` |
| `lean-eval-recorder` | `kim-em` / `3769615` | Metadata read, Contents write | Selection remains `leanprover/lean-eval-submissions`; `<compact readback>` |
| `lean-eval-archiver` | `kim-em` / `3856297` | Metadata read, Contents write | Selection remains exactly `leanprover/lean-eval-audit`; `<compact readback>` |

The two source-read paths may retain `leanprover/lean-eval-state-staging` only
through the named production canary. Remove that repository from both
selections after the canary is terminal and its source fixture has no dependent
run. Do not broaden any App merely to satisfy a preflight.

## 5. Submitter-facing contract

The authoritative text is on the [submission entry page][entry], the stable
[production application][application], and in the [Worker form
implementation][worker-form]. It tells submitters that:

- they must have authority to provide the exact source and must not submit
  secrets or material they cannot disclose;
- LeanEval stores and runs source privately for evaluation, but confidentiality
  is best-effort rather than guaranteed;
- evaluation metadata and results become public;
- `scheduled` is the default publication choice and confirms authority to
  license accepted source under Apache License 2.0 exactly two UTC calendar
  months after acceptance; and
- `withheld` keeps accepted source private while leaving the public result;
  the submitter may later schedule release, and a scheduled choice cannot be
  changed back to private.

[entry]: https://lean-lang.org/eval/submit/
[application]: https://lean-eval-submission-server.lean-eval.workers.dev/
[worker-form]: https://github.com/leanprover/lean-eval-submissions/blob/main/server/src/browser-ui.ts

## 6. Rollback and emergency pause

1. For intake or lifecycle failure, dispatch
   [`intake-disable-recovery.yml`](../.github/workflows/intake-disable-recovery.yml).
   It deploys only the all-false configuration, clears both maintainer arrays,
   and verifies the exact recovered version and effective public health.
2. For a coherent code rollback, use
   [`rollback-worker.yml`](../.github/workflows/rollback-worker.yml) with one
   reviewed target commit and its broker, replay, and intake version IDs. Keep
   intake disabled throughout.
3. For publication pause, remove or set `PUBLICATION_ENABLED=false`, cancel
   every queued/running controller run, and reconcile any committed
   `release.started` event. For an emergency stop, also block
   `release-production` or revoke its scoped release keys/AWS role.
4. A rollback never rewrites State, Results, releases, audit objects, AWS
   resources, credentials, or Git history. Forward-deploy or rerun recovery to
   restore one coherent component set.
5. After recovery or rollback, read back `/healthz` and make the authenticated
   `POST /readyz` State-writer proof: intake, broker, and general replay must be
   false, the recovered Worker version must match the recorded deployment, and
   State validation must still pass. If publication was paused, also read back
   its independent gate and verify that production publication cannot proceed
   before investigation continues.

## 7. Deferred functions and launch limitations

- Historical corpus replay and legacy private-envelope migration continue
  after launch and do not block new schema-version-3 submissions.
- GitHub issue intake remains available for the four-week overlap.
- Model consolidation stays disabled. FC integration, disproof support,
  experimental kernels, persistent qualification machinery, automatic copycat
  detection, a second key provider, and verified-calculation runner
  infrastructure are outside this overhaul.
- The neutral open-problems tab may be empty.
- Browser OAuth and several credential sets have one temporary human custodian;
  the recorded pause/recovery paths are the launch mitigation.
- The production State writer is a user-scoped GitHub token whose API allowance
  shares its issuer's rate bucket. Exhaustion makes readiness and intake fail
  closed until capacity returns; avoid unrelated high-volume authenticated API
  work from that principal whenever the server is in operational use. A later
  dedicated GitHub App installation-token path would remove this coupling, but
  is an availability improvement rather than a launch gate under the accepted
  completion plan.

## 8. Repository announcement

Publish only after the first four production actions have succeeded. The
overlap starts at the recorded announcement-publication timestamp, not at an
earlier capability-enablement time. Before publishing, calculate and record
the explicit UTC calendar closure date 28 days after that timestamp; do not
publish a relative-date placeholder. Repository publication is covered by
standing authorization; a Zulip post still requires separate exact approval.

The launch-copy candidate is draft `leanprover/lean-eval` PR `#592` at exact
head `00b91e4f0943f0212d9184a951d3a08a1ab4244e`. Keep it draft until the
announcement timestamp and explicit target closure date replace every relative
date, and require all conditions below in its exact final diff before merge.

The announcement must include all of the following reviewed facts:

- the static leaderboard entry URL,
  **https://lean-lang.org/eval/submit/**, and the stable lifecycle-aware
  submission application URL,
  **https://lean-eval-submission-server.lean-eval.workers.dev/**;
- the explicit target issue-intake closure date and the condition that issue
  intake closes only after the incident, adoption, overlap, and two-week notice
  gates pass;
- authenticated exact-ref intake, initially private evaluation-group source,
  public evaluation metadata and results, and best-effort confidentiality;
- the `scheduled` Apache-2.0 release policy, exactly two UTC calendar months
  after acceptance, the `withheld` private-source choice, and the irreversible
  later transition from private to scheduled;
- lifecycle amendments, model aliases, publication choice, and status; and
- a request to report server problems by
  [opening a submissions issue](https://github.com/leanprover/lean-eval-submissions/issues/new),
  with any revised overlap date announced explicitly.

## 9. Finalization record

The fields below are a pending template. Bind the repair baseline, lifecycle
candidate, final staging exercise, and fresh held deployment, then change the
top-level status to `GO` only after every gate is established. The
production-launch fields are the post-action finalization record and must be
filled immediately after each numbered production action.

```text
production release trust:
  protected release commit: 3c68d99f3de7060f7f0fdacf9340354775546c05
  protected audit commit: f50c46574dd719486a01272e3eaeced396ac5ada
  protected production State: 9cf3b4999bae2b6faaa32ff1bf5f040c5e6f787f
  repaired/read back at: 2026-09-01T15:11:16Z
  publication-disabled preflights:
    controller: https://github.com/leanprover/lean-eval-releases/actions/runs/33524122775
    audit read: https://github.com/leanprover/lean-eval-releases/actions/runs/33524126922
    OIDC trust: https://github.com/leanprover/lean-eval-releases/actions/runs/33524130789
  release queue at protected State:
    event count: 489
    source digest: d3392218297ea11f6093b59e5252f3ae394887368e02cea40a58e6fd82a901b
    task count: 0
    empty-controller evidence: https://github.com/leanprover/lean-eval-releases/actions/runs/33525394698
  publication latch: absent at 2026-09-01T17:34:47Z
  environment variable:
    AWS_RELEASE_UNWRAP_ROLE_ARN: arn:aws:iam::161072922960:role/lean-eval-release-unwrap-invoker-production
  environment secret names: AUDIT_READ_KEY / PRODUCTION_STATE_CONTROLLER_KEY / RELEASE_PUBLISH_KEY

final exact-version staging:
  replacement repair merge commit: 451856ebdd4ca4d875e43be7cd113678dea9e1b7
  replacement repair protected CI: https://github.com/leanprover/lean-eval-submissions/actions/runs/33535007875
  replacement repair deployment: https://github.com/leanprover/lean-eval-submissions/actions/runs/33535007713
  replacement repair production versions:
    intake: 7afc61bf-6427-431a-b4f6-c1c3ec2641ac
    broker: dfd77e4f-16ae-4a63-81ab-bbb79797385b
    replay: 570664e6-a6f5-428d-87cb-803dd5b1768f
  lifecycle candidate commit: 38bd445d2242e71d1d09a304c1c1e78d987895a0
  immutable dispatch tag: lean-eval-dispatch/38bd445d2242e71d1d09a304c1c1e78d987895a0
  protected exact-head CI: https://github.com/leanprover/lean-eval-submissions/actions/runs/33536577682
  tracked production lifecycle configuration:
    six launch gates: true / true / true / true / true / true
    result-amendment maintainers: [{"github_id":477956,"login":"kim-em"}]
    model-identity maintainers: [{"github_id":477956,"login":"kim-em"}]
    intake configured/effective/mode/lease: false / false / disabled / absent
    consolidation / release opt-out / promotion canary: false / false / false
    general replay / historical-public replay / production acceptance: false / false / false
  tracked staging configuration:
    intake and six lifecycle gates: all false
    both maintainer arrays: []
    consolidation / release opt-out: false / false
  bounded lifecycle smoke:
    status: NOT YET BOUND; packet remains NO-GO
    recovery watchdog: https://github.com/leanprover/lean-eval-submissions/actions/runs/33537589154
    enabled-state deployment: https://github.com/leanprover/lean-eval-submissions/actions/runs/33538013088
    completed functional smoke: <successful terminal operator result>
    disabled-state recovery: <successful Actions URL>
    final all-false health/readiness: <UTC timestamp and compact readback>
    post-recovery staging Worker versions:
      intake: <UUID>
      broker: <UUID>
      replay: <UUID>
  fresh exact-lifecycle held deployment:
    run: <exact-38bd Actions URL started after smoke recovery>
    check job: <successful job URL>
    tag verification job: <successful job URL>
    staging deployment job: <successful job URL>
    promotion canary job: <successful job URL>
    production job: <waiting cloudflare-production job URL; not yet approved>
    staging Worker versions:
      intake: <UUID>
      broker: <UUID>
      replay: <UUID>
    staging config/readback: <all launch gates and intake false, arrays empty>
  App access readbacks:
    source-reader production broker: <successful exact private-fixture preflight URL>
    workflow-dispatcher installation 155329316: <submissions-only compact readback>
    evaluation source App: <successful exact branch/commit preflight URL>
    recorder submissions-only selection: <compact readback>
    archiver audit-only selection: <compact readback>
  protected docs-only packet merge: <40-character protected-main descendant of lifecycle candidate>
  fresh all-false recovery: https://github.com/leanprover/lean-eval-submissions/actions/runs/33535723990
  heads observed during smoke:
    production State: 9cf3b4999bae2b6faaa32ff1bf5f040c5e6f787f
    staging State: bba17baad28c61b03311f20080f0ffbad4f74656
    production Results: 38bd445d2242e71d1d09a304c1c1e78d987895a0
    staging Results: 06bfd1ed3f7a11db5cb33f5a581330077e55e80e
  final post-recovery staging State and Results: <40-character SHAs; re-read after smoke>

production launch:
  action 1 - release controller:
    enabled/read back at: <UTC timestamp and URL>
    protected release/State/audit heads: <unchanged exact SHAs>
    queue: <zero tasks immediately before and after latch>
  action 2 - lifecycle production deployment:
    approved held job: <same job URL bound before packet merge>
    approved/completed at: <UTC timestamps>
    runtime commit: 38bd445d2242e71d1d09a304c1c1e78d987895a0
    production Worker versions: <intake, broker, replay>
    protected config readback:
      six launch gates: true / true / true / true / true / true
      result-amendment maintainers: [{"github_id":477956,"login":"kim-em"}]
      model-identity maintainers: [{"github_id":477956,"login":"kim-em"}]
    public health/readiness readback:
      six launch gates: true / true / true / true / true / true
      intake configured/effective/mode/lease: false / false / disabled / absent
      consolidation / release opt-out / promotion canary: false / false / false
      general replay / historical-public replay / production acceptance: false / false / false
    readback timestamp and URLs: <UTC timestamp and compact URLs>
  action 3 - production intake:
    draft PR/head before merge: 1600 / 0c80159bd060167694db8200fc2f115893775482
    protected merge/CI: <40-character SHA and successful Actions URL>
    exact seven-file scope revalidated: <compact result>
    protected staging promotion canary: <successful Actions URL>
    finite lease activated/read back at: <UTC timestamp and successful URL>
    durable transition/read back at: <UTC timestamp and successful URL>
    production Worker versions: <intake, broker, replay>
    lifecycle and false-gate readback unchanged from action 2: <compact result>
  action 4 - production canary:
    source repository: leanprover/lean-eval-state-staging
    source ref: production-canary-source-fixture-v4
    source commit: 18595c8d06ec29d47940065644d16ae7cfbd1591
    problem: formalization-evaluation/substInv_X_sub_X_sq_eq_catalan@1
    model: LeanEval production launch canary
    publication sequence: private, then visible irreversible publication opt-in
    submission/result: <UUIDv7 and terminal result URL>
    archive/evaluation/Result/State/leaderboard: <compact terminal evidence>
    production State commit after validation: <40-character SHA>
    production health/readiness: <URL>
    temporary source/App access cleanup: <branch deletion and both source selections removed>
  repository announcement: <URL>
  announcement published at: <UTC timestamp>
  target issue-intake closure date: <YYYY-MM-DD>
```
