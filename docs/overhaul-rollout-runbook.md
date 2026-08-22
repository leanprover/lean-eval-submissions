# LeanEval overhaul rollout runbook

This runbook separates work that is technically ready from decisions and
credential operations that require a maintainer. It supplements
[`INFRASTRUCTURE.md`](../INFRASTRUCTURE.md); the infrastructure ledger remains
the source of truth after resources are created.

Terminology: unqualified **v1** and **v2** name problem sets. The platform
work is the **lifecycle overhaul**, and machine-format versions are always
qualified as **schema version 2**. Frozen identifiers and filenames are not
renamed.

For the short, current list of UI and secret-entry work, use
[`overhaul-manual-setup.md`](overhaul-manual-setup.md). The longer sections
below retain decision rationale and the full rollout sequence.

Do not put token values, private keys, recovery material, or OAuth client
secrets in this file, an issue, a pull request, or a terminal transcript.

## Current implementation checkpoint

The reviewed foundations and active pull requests are published. The user's
primary checkout is not the integration workspace.

| Repository / lane | Published commit or pull request |
| --- | --- |
| `lean-eval-generator` | `77373a539b31f8f304c852f288d7d8469cceebff` on `main`; fixes `#1` / `#2` and synchronization `#3` are merged and green; merged LeanEval consumer `#553` pins this exact commit and removes the embedded core |
| production State | `d6d566bd8f92f157bee68d3890aaf8a30c339c9d` on private `main`; strict redacted public projection enabled and schema terminology qualified |
| staging State | `1e76ea66405ed692bf7564f5accbfb3efd9c25f0` on private `main`; full deliberate-rejection lifecycle and projection contract recorded, with schema terminology qualified |
| `lean-eval-releases` | `f1f83344017333650b4066a533e5ff4eefda5b54` on `main`; terminology `#3`, planner `#1`, and reconstruction `#2` are merged and green; publication remains disabled |
| catalog, generator consumer, software verification | v1 freeze merged as `lean-eval#540`; final 128-member v1 set merged in `#548`; terminology rule merged in `#554`; standalone-generator consumer merged in `#553`; current main `b91d4757aa0d7776c02540c9089df54fa0d0658a` |
| results schema version 2, intake server, replay contracts | foundations merged in `lean-eval-submissions#1165`; lifecycle status in `#1190`; archive-before-evaluation in `#1198`; exact-blob archive verification and dependency promotion in `#1213` / `#1214`; runtime-only deployment trigger in `#1217`; infrastructure refresh in `#1225`; schema terminology in `#1227`; kernel/AWS smokes `#1207` / `#1208` merged and deployed as exact runtime `a34b2053ce8c4e7e9833d57de893ab2aa62e797b` with intake disabled |
| AWS archive-key custody | dedicated account `lean-eval` (`161072922960`), exact GitHub OIDC provider, and isolated staging/production CloudFormation stacks provisioned 2026-08-22; only staging role variables connected; no replay compute and no production workflow connected |
| lifecycle-aware leaderboard | preview foundation merged as `lean-eval-leaderboard#69`; UI terminology merged in `#73`; deeper schema terminology merged in `#74`; cutover `#72` is merged and live at `https://lean-lang.org/eval/`, with `/legacy/` retained and read-only State deploy key `160968617` provisioned |

The private broker and intake Workers are deployed in staging and production
from exact commit `a34b2053ce8c4e7e9833d57de893ab2aa62e797b`.
Deployment, OAuth,
readiness, authentication, State-writer, and broker App secrets are installed.
Both State-writer tokens are organization-approved and preflighted, and both
broker App registrations transferred to `leanprover` without changing their
IDs or the dispatcher installation. The trusted archive job now persists the
encrypted snapshot before any evaluation job can start. Both Workers are
currently intake-disabled. Browser and headless staging intake, exact-ref
dispatch, both private-read paths, and the UUIDv7 archive callback have been
exercised. The separate `lean-eval-bot` (App ID `3346375`, not Source Reader
App ID `4666604`) is installed on the private fixture and both archival and
evaluation fetches succeed. Exact Worker
version identifiers are recorded in `INFRASTRUCTURE.md`. The live results store
has not been migrated. Dry-run `32442394883` reports source commit
`91c55f3c1a515f87f33b3f8c45a4fd4565a0028f`, 44 files / 1,285 records,
source digest `9c6ab2e17186d4498d33816010b01ba330122d0863efa300d7de6aaf07356db4`,
output digest `340eaa0cce486aed35874ae1571425cb6e8912009f99822ea75fa945ea931a9e`,
and no duplicate result IDs. That report is now historical because the live
store advanced; D7 requires a fresh report after the maintainer lifts the hold.
D7 remains explicitly unapproved and unapplied.

The dedicated AWS account now holds the KMS keys, conditional one-use tables,
and direct-Lambda unwrap gates described by D6. This is archive-key custody,
not an AWS evaluation backend. Production role variables remain unset, and the
separate provider-neutral disposable replay executor remains unselected.

The FC-owned importer in `formal-conjectures#4951` now imports, verifies,
classifies, and generates all FC100 declarations through the frozen generator
contract. Its current baseline audit builds 97/100 Challenges at LeanEval pins
with three exact registered failures. Generator fixes `#1` / `#2` retire the
Erdos125 failure, and the separately verified FC toolchain bump retires the
remaining two. Launch still waits for those exact pins, FC maintainer review,
and the production gates; the compatible output contract itself is no longer
missing.

## Decisions required from maintainers

Record each answer in the implementation tracker before creating resources.

### D1: production and staging State visibility

The public implementation plan says all four new repositories are public, but
`INFRASTRUCTURE.md` currently describes both State repositories as private.
This must be resolved before repository creation.

- **Private raw State (recommended):** keep authenticated intake, source
  repository names and commits, failures, and operational events private;
  publish only a reviewed materialized projection. Generator and releases stay
  public. This has the smaller accidental-disclosure surface.
- **Public raw State:** provides maximum auditability, but explicitly reveals
  every field permitted by the event schema, including private-repository names
  and commit hashes. Choosing this requires accepting that disclosure or first
  redesigning the intake event into a public-safe projection.

Decision to record: `production State = public|private`,
`staging State = public|private`, and whether a separate public projection is
required.

**Resolved 2026-08-20:** production State and staging State are private. A
reproducible, schema-validated public projection is required. Raw events remain
the authority; the projection must carry its source commit and event count and
fail closed rather than publish an incomplete materialization.

### D2: v1 membership and cutoff

At the 2026-08-20 audit snapshot there are 289 visible formalization problems:

- 91 have no accepted solve;
- 174 have fewer than five accepted solves;
- 121 have fewer than five accepted solves **and no recorded public
  submission**;
- 262 have fewer than ten accepted solves;
- all 289 would reproduce the current visible catalog.

Recommended starting rule: include the 121 problems with `visible = true`,
`solve_count < 5`, and `public_submission_count = 0`, then have a maintainer
review that candidate for misformalizations and explicit exceptions. This
implements the stated intent that already-common or publicly leaked problems
do not enter v1 without reducing v1 to only never-solved problems.

**Mechanical rule resolved 2026-08-20:** include problems with
`visible = true`, `group = "formalization-evaluation"`, `solve_count < 3`, and
`public_submission_count = 0`. This selects 118 candidates in the audit over
1,279 accepted records at results commit
`9d3fa0a138aa5bb667669b6c10dbe0c2d101b2bc`. The exact count and digest must be
recomputed at the final freeze commit; cutoff, exceptions, and freeze timing
remain to be recorded.

**Freeze procedure resolved 2026-08-20:** open the freeze PR from a named
results commit, review the stable candidate list, rerun against the latest
results commit immediately before merge, and review any delta. Record the final
commit, record count, candidate digest, and documented exclusions. No additions
outside the mechanical rule are permitted for v1. Membership becomes
irreversible when the reviewed PR merges; later defects are represented by
status/history rather than rewriting the set.

Decision to record:

1. the UTC cutoff and results-store commit;
2. the mechanical inclusion rule;
3. explicit additions/exclusions and reasons;
4. the publication date at which `frozen = true` becomes irreversible.

### D3: software-verification statements

For each draft, choose `approve`, `revise`, or `reject` after a human reviews
the trusted statement rather than only whether Lean accepts it:

- `coc_strong_normalization` / `CoCStrongNormalization.lean`;
- `rcf_quantifier_elimination` / `RealClosedFieldQE.lean`.

Review the quantified domain, the load-bearing obligations, whether a trivial
implementation satisfies the interface, and whether the prose makes a claim
stronger than the Lean statement. Approval moves the manifest from `draft` to
`active`; it does not authorize agent-written hints.

**Resolved 2026-08-20:** approve both statements and move both manifests to
`active`. Research and an independent Fable review found no mathematical or
encoding defect in either formal core. Retain both problems. Rename the RCF
problem to “Quantifier elimination for the theory of real closed fields” and
correct its free-variable, enumeration, generalization, and prior-CAD prose.
Describe the CoC system precisely as non-cumulative Π-only CCω, correct the λC
warm-up, and acknowledge prior mechanizations and semantic models. At the
maintainer's direction, add three conservative anti-vacuity requirements:
`typing_polyId_app`, `step_polyId_app`, and `holds_ex_sq`. These exercise
application typing, beta substitution, and the RCF de Bruijn semantics. All
three statements were mechanically proved before inclusion. Because neither
draft has been published, these edits retain `statement_revision = 1`.

### D4: Cloudflare ownership

Wrangler is currently authenticated to account
`d789bf36d237e0cb313be59b927c82bd` (`Kim@lean-fro.org's Account`). That does
not establish ownership of the `lean-lang.org` zone.

Recommended long-term choice: deploy from an organization-controlled account
that owns `lean-lang.org`. The temporary dedicated Lean Eval account keeps this
work isolated from unrelated services.

Decision to record: Cloudflare account name and ID, zone ID, administrator, and
cost owner.

**Temporary account choice resolved 2026-08-20:** the initially selected
`Kim@lean-fro.org's Account`
(`d789bf36d237e0cb313be59b927c82bd`) must not host Lean Eval. Its
`palomar-server.workers.dev` subdomain already serves four unrelated Palomar
Workers, so renaming it or publishing Lean Eval beneath it creates the wrong
ownership boundary. The separate Free account `lean-eval`
(`a46b90978a1c29cc4795f30677e7e4b8`) with subdomain
`lean-eval.workers.dev` now isolates the intake-disabled bootstrap without
changing Palomar URLs. The checked-in provider-neutral contracts preserve a
later move to an organization-controlled account or a different provider.

**Personnel recorded 2026-08-20:** administrator and temporary cost owner are
Kim Morrison (`kim@lean-fro.org`). The zone ID and migration date remain open.

**Account inventory recorded 2026-08-20:** read-only Cloudflare API queries
returned no `lean-lang.org` zone and found four existing Palomar Workers in the
current account: `palomar-data`, `palomar-data-staging`,
`palomar-domain-redirect`, and `palomar-server`. The planned
`eval-submit-staging.lean-lang.org` and
`eval-submit.lean-lang.org` custom domains therefore cannot be deployed from
either the current or new temporary account. The checked-in Wrangler
environments now enable only the two exact `lean-eval.workers.dev` routes;
preview URLs and intake remain disabled.
The custom domains will be configured when the organization account is ready.

**Temporary endpoint choice resolved 2026-08-20:** use
`lean-eval-submission-server-staging.lean-eval.workers.dev` and
`lean-eval-submission-server.lean-eval.workers.dev` for the two
intake-disabled environments; never use `palomar-server.workers.dev`. The
environment routes, exact OAuth callbacks, CI smoke URLs, and rollback URL are
configured in lockstep. These public development endpoints do not authorize
deployment by themselves and must be replaced by the reviewed `lean-lang.org`
custom domains during the organization-account migration.

### D5: initial State writer credential

The implemented bootstrap uses separate Kim-owned, fine-grained PATs because a
static GitHub App installation token expires. Each token is limited
to one State repository, Contents read/write and Metadata read, and at most a
90-day lifetime.

- **Fine-grained PAT bootstrap (recommended for intake-disabled rollout):**
  operationally simple, with a mandatory rotation date and machine owner.
- **GitHub App before bootstrap:** requires implementing and reviewing App JWT
  signing and installation-token refresh or a token broker first.

Decision to record: credential model, machine owner for each environment,
expiration, rotation owner, and whether production intake is forbidden until
the GitHub App replacement exists.

**Resolved 2026-08-20:** bootstrap with two distinct fine-grained PATs owned by
Kim Morrison, one for staging State and one for production State. Each token is
restricted to its single private State repository with Contents read/write and
Metadata read, expires no more than 90 days after creation, and is rotated by
Kim Morrison no later than 14 days before expiry. Record the exact token owner,
creation date and expiry date in
`INFRASTRUCTURE.md` when each repository and token exists. Never reuse either
token across environments or grant workflow/repository administration. These
PATs are approved for initial staging and production State writes after
organization approval and an environment-specific write test. The only
ruleset bypass is the specific `kim-em` user, whose two PATs remain separated by
their single-repository scopes. Reassess and rotate or replace both credentials
no later than 14 days before expiry.

### D6: publication, key lifecycle, and license policy

**D6a resolved 2026-08-20:** use AWS KMS in a new dedicated AWS account. AWS is
an implementation choice, not part of the stable data model. Each submission
keeps an ordinary `age` archive and a small provider-neutral envelope containing
the submission ID, archive digest, recipient, adapter name, and opaque wrapped
identity. AWS-specific identifiers stay inside the AWS adapter. Stable archive
paths, State subjects, result IDs, replay IDs, and capability claims do not
contain AWS identifiers.

Replay and release code use a small wrap/unwrap interface. A later provider
migration, while AWS is still available, unwraps each small identity with the
AWS adapter and wraps it with the replacement adapter; archives and stable IDs
do not change. No new State event or dual-provider scheme is required.

**D6b resolved 2026-08-20:** there is no provider-loss recovery mechanism. If
AWS becomes permanently unavailable before migration, the affected private
archives may be unrecoverable. This is an accepted simplification.

The dedicated account and both isolated key-adapter stacks were provisioned on
2026-08-22; their exact identifiers and live verification evidence are recorded
in `INFRASTRUCTURE.md`. The
provider-neutral envelope, ten-minute replay/release capability claims, stable
`ak1_` identity, `uc1_` audit digest, and consume-before-unwrap interface are
frozen in `schemas/archive-key-envelope-v1.schema.json`,
`schemas/unwrap-capability-v1.schema.json`, and
`docs/key-capability-contract.md`. `scripts/archive_envelope.py` implements
the trusted, provider-neutral archive preparation side: one fresh PQ-hybrid age
identity per archive, strict stdin-only adapter wrapping, and atomic publication
of ciphertext plus envelope. `scripts/aws_key_adapter.py` and
`infrastructure/aws-key-adapter/template.yaml` implement the initial KMS wrap,
direct-Lambda unwrap, and conditional DynamoDB consume boundary. Only the
staging smoke has role variables; production remains disconnected. The
disposable execution backend remains a separate unprovisioned launch gate.

**Contributor acknowledgement approved 2026-08-20:** “By submitting, I confirm
that I have authority to provide this source. I authorize Lean Eval to store
and run it privately for evaluation, publish evaluation metadata and results,
and, two UTC calendar months after acceptance, publish the submitted source
under the Apache License 2.0. I will not submit secrets or material I am not
authorized to disclose.” Keep this acknowledgement adjacent to the submit
action rather than expanding it into a separate policy questionnaire.

### D7: live results schema version 2 migration

Workflow dry run `32442394883` preserved 1,285 of 1,285 records across 44
files at submissions commit
`91c55f3c1a515f87f33b3f8c45a4fd4565a0028f`, with no duplicate IDs, source
digest `9c6ab2e17186d4498d33816010b01ba330122d0863efa300d7de6aaf07356db4`,
and output digest
`340eaa0cce486aed35874ae1571425cb6e8912009f99822ea75fa945ea931a9e`.
That evidence becomes stale if any results file on `main` changes.

Authorization must name the fresh workflow run and approve its exact source
commit, record count, and output digest. `apply=true` is a separate decision
from merging the migration tooling. After the first schema version 2-only
record lands, repairs are forward-only.

### D8: two correctness-preserving plan deviations

The implementation intentionally differs from two literal details in the
public program:

1. State events use `events/<uuid-prefix>/<event-id>.json`, not a date-derived
   path. An event ID therefore has exactly one possible path even if a retry is
   reconstructed on another UTC day. UUIDv7 still carries sortable time, while
   the event's canonical `occurred_at` remains authoritative.
2. Record jobs do not share a static Actions concurrency group with migration.
   [The default concurrency queue retains only one pending run](https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency),
   and even the newer `queue: max` mode has a finite queue. Instead, migration installs a
   durable compare-and-swap Git lock in the results store; every record job
   waits on that lock and retains its independent per-submission run. The
   workflow still names the logical `results-store-writer` contract.

**Approved 2026-08-20:** both deviations preserve the intended
single-path/idempotency and no-dropped-writer invariants more strongly than the
literal mechanisms.

### D9: GitHub App and token-broker boundary

The Worker implementation deliberately keeps production intake disabled while
`GITHUB_VERIFICATION_TOKEN` and `GITHUB_DISPATCH_TOKEN` are only local contract
hooks. A long-lived PAT in either binding is not an approved production design.
The server needs two distinct capabilities:

- read repository metadata and tags for source verification; and
- dispatch only `submission.yml` at the reviewed immutable
  `lean-eval-dispatch/<commit>` tag in `lean-eval-submissions`.

**Approved 2026-08-20:** create separate least-privilege GitHub Apps and let an
organization-operated broker mint their short-lived installation tokens. Reach
the broker through an authenticated Cloudflare service binding or equivalent
private machine-to-machine channel. The Worker must send an explicit audience,
operation, repository, and immutable commit; the broker must reject every
permission or repository outside the corresponding App installation. This
keeps App private keys out of the public intake Worker and prevents the source
reader from acquiring workflow-dispatch authority.

The alternative is in-Worker App JWT signing. Choosing it authorizes a design
change to store separate App IDs, installation IDs, and private keys as Worker
secrets and to implement token minting, expiry caching, and 401 refresh there.
Do not substitute a user PAT merely to avoid this choice.

Use two Apps: one source reader and one workflow dispatcher. Record App IDs,
installations, exact permissions, and broker identifiers when provisioned;
private keys never enter this ledger.

Implementation note: GitHub calls these “secret” gists, but documents them as
unlisted rather than private. Agent proof anonymously fetches the exact gist ID
and validates `public: false`, the exact owner, an untruncated
`lean-eval-proof.txt`, and the signed expiring challenge. The broker continues
to reject `/gists/`; no App or user token gains gist authority, and browser
OAuth remains `read:user` only.

### D10: embargo and issue-intake transition policy

The disabled release tooling currently implements the plan's proposal as two
UTC calendar months from `accepted_at` (not a fixed 60-day duration). The plan
also proposes keeping issue intake open for four weeks after production server
intake begins. Neither proposal should silently become policy through code.

**Approved 2026-08-20:** use two UTC calendar months and initially plan a
four-week issue-intake overlap after server intake begins. Four weeks is a
target, not an irrevocable minimum: a later explicit maintainer decision may
close issue intake earlier, provided the closure is announced and the server
path is functioning. Project maintainers may pause intake or releases when
needed.

## External actions that require explicit authorization

The following are separate writes. Authorization for one does not imply the
others:

1. create the four repositories using the visibility selected in D1;
2. push each existing local commit history;
3. create the one `[overhaul]` tracker issue from
   [`overhaul-tracker.md`](overhaul-tracker.md);
4. push implementation branches to existing repositories;
5. open draft pull requests;
6. merge pull requests;
7. provision GitHub environments, rulesets, machine users, or secrets;
8. deploy either Cloudflare Worker;
9. execute the results migration with `apply=true`;
10. enable intake, replay, or publication.
11. restore the accidentally advanced local leaderboard branch from
    `1ab445c2fc96bd7aa252ec9c86cd7c99a40c0f3e` to its recorded pre-accident
    head `66a80478bb2af07693ccdb394d7e24dc9115298b` (after creating a backup ref).

**Authorization recorded 2026-08-20:** the maintainer authorized repository
creation and publication, tracker creation, implementation branch pushes,
draft pull requests, merges after required checks, infrastructure provisioning,
intake-disabled Worker deployment, eventual feature enablement after their
documented safety gates, and the backed-up leaderboard-branch repair. D7 remains
separately bound to a fresh, named migration report because the maintainer asked
for clarification before the irreversible rewrite. Secret values and manual
account actions still require the operator walkthrough at the point of use.

## Maintainer response template

Copy this block into the tracker and replace every placeholder. A blank answer
means the dependent rollout step stays disabled.

```text
D1 State: production=<public|private>, staging=<public|private>,
          public projection=<yes|no>
D2 v1: cutoff=<UTC timestamp>, results commit=<SHA>,
       rule=<rule>, exceptions=<list/link>, freeze date=<date>
D3 statements: coc_strong_normalization=<approve|revise|reject>,
               rcf_quantifier_elimination=<approve|revise|reject>
D4 Cloudflare: account=<name/id>, zone=<id>, primary admin=<name>,
               backup admin=<name>, cost owner=<name>
D5 State writer: <fine-grained PAT bootstrap|GitHub App>,
                 staging owner/expiry=<...>, production owner/expiry=<...>,
                 rotation owner=<name>
D6 security/legal: KMS/HSM=<provider/account/key>, issuer=<design>,
                   provider-loss recovery=none, legal wording=<approved link>
D7 migration: workflow run=<URL>, source commit=<SHA>, count=<integer>,
              output digest=<SHA-256>, apply=<authorized|not authorized>
D8 deviations: UUID-prefix State paths=<approved|rejected>,
               durable Git writer lock=<approved|rejected>
D9 GitHub Apps: architecture=<broker|in-Worker signing>,
                source App/installation=<...>, dispatch App/installation=<...>,
                broker identity/audience=<...>, rotation owner/date=<...>
D10 policy: embargo=<two UTC calendar months|replacement>,
            issue window=<four weeks|replacement>, approvers=<...>,
            announcement/notification=<...>, emergency pause owner=<...>

External authorization (answer each yes/no):
- create repositories: <yes|no>
- push new-repository histories: <yes|no>
- create tracker issue: <yes|no>
- push existing-repository branches: <yes|no>
- open draft PRs: <yes|no>
- provision GitHub/Cloudflare resources: <yes|no>
- deploy intake-disabled Worker: <yes|no>
- restore accidental leaderboard branch: <yes|no>
```

## Repository bootstrap walkthrough

Run only after D1 and explicit repository-create and push authorization.
Substitute `--private` for the two State repositories if D1 selects private
raw State.

```bash
gh repo create leanprover/lean-eval-generator --public \
  --description "Deterministic workspace generator for LeanEval"
gh repo create leanprover/lean-eval-state <STATE_VISIBILITY_FLAG> \
  --description "Append-only production State for LeanEval"
gh repo create leanprover/lean-eval-state-staging <STATE_VISIBILITY_FLAG> \
  --description "Synthetic staging State for LeanEval"
gh repo create leanprover/lean-eval-releases --public \
  --description "Delayed public LeanEval source releases"
```

Before each push, verify the exact clean source and HEAD:

```bash
git -C <LOCAL_REPOSITORY> status --short
git -C <LOCAL_REPOSITORY> log -1 --oneline
git -C <LOCAL_REPOSITORY> remote add origin \
  https://github.com/leanprover/<REPOSITORY>.git
git -C <LOCAL_REPOSITORY> push -u origin main
```

After publishing `lean-eval-generator`, replace the development path dependency
in the LeanEval consumer with the exact public commit SHA, refresh
`lake-manifest.json`, rerun byte-for-byte golden parity, and only then publish
the LeanEval consumer branch. The trusted main regenerator, rather than the
source PR, refreshes all tracked generated workspaces.

## State repository setup walkthrough

For each State repository:

1. Set `main` as the default branch.
2. Create an active ruleset targeting `main` that blocks deletion and
   non-fast-forward updates, requires linear history and a pull request for
   human changes, and requires the repository's validation check.
3. Put only the environment-specific writer principal or writer team in the
   ruleset bypass list. Do not grant the other environment's writer access.
4. Run the repository validator before enabling intake.

Create each fine-grained PAT in GitHub's UI so its value never appears in a
command history or transcript. Select only its State repository, Metadata read,
and Contents read/write. Record owner, creation, expiry, and rotation dates—but
not the value—in `INFRASTRUCTURE.md`.

## Cloudflare and GitHub environment walkthrough

These steps use the dedicated Lean Eval account and the two scoped deployment
tokens. Custom domains can be added later without changing the API contracts.

1. Ensure the operator is a member of the correct Cloudflare account, then run:

   ```bash
   cd server
   npx wrangler whoami --json
   ```

   Stop unless the selected account ID is present.

2. In the Cloudflare dashboard, create distinct staging and production API
   tokens. Restrict them to the dedicated `lean-eval` account and grant only
   Workers Scripts edit. That permission is account-scoped rather than
   per-script, so the dedicated account—not a fictitious per-Worker resource
   filter—is the isolation boundary. Do not grant zone permissions or reuse a
   personal global API key.

3. Create the protected GitHub environments:

   ```bash
   gh api --method PUT \
     repos/leanprover/lean-eval-submissions/environments/cloudflare-staging
   gh api --method PUT \
     repos/leanprover/lean-eval-submissions/environments/cloudflare-production
   ```

   Restrict both environments to deployments from protected `main`. Do not add
   a second production reviewer gate unless the deployment policy is
   deliberately changed: under the approved design, merging to protected
   `main` is the human authorization and production follows automatically only
   after staging passes.

4. Create the `submission-dispatch-promotion` GitHub environment, require a
   maintainer reviewer, and restrict it to protected `main`. In repository
   Actions settings, permit workflows to request read/write `GITHUB_TOKEN`
   access; the promotion job itself narrows that token to only
   `contents: write`. Add a distinct 32-byte lowercase-hex
   `DISPATCH_PROMOTION_APPROVAL_GUARD` secret to this environment only (never
   at repository or organization scope). The value grants no API access; its
   sole purpose is to make a missing or accidentally auto-created unprotected
   environment fail closed before tag creation.

   Create an active tag ruleset whose target is
   `refs/tags/lean-eval-dispatch/*`. Allow tag creation, but block updates and
   deletion; do not grant the Worker, deployment token, dispatch broker, or
   ordinary maintainers a bypass. Record the ruleset ID and required-reviewer
   owners in `INFRASTRUCTURE.md`.

   After `check` succeeds, `deploy-worker.yml` enters that environment and uses
   its least-privilege `GITHUB_TOKEN` to create
   `lean-eval-dispatch/<GITHUB_SHA>`. It first proves the commit is reachable
   from `main` and contains `submission.yml`. If the tag already exists, the
   job succeeds only when it resolves to the same commit; a collision fails the
   deployment. The tag is read back and passed as `DISPATCH_WORKFLOW_REF` to
   both Wrangler deployments. Missing approval, insufficient token policy,
   missing ruleset setup, collision, or read-back mismatch stops deployment;
   intake remains false.

5. Add `CLOUDFLARE_ACCOUNT_ID` and the environment-specific
   `CLOUDFLARE_API_TOKEN` as GitHub **environment secrets**. Enter values from a
   protected local file or interactive prompt; never use `echo` or paste them
   into an issue:

   ```bash
   gh secret set CLOUDFLARE_ACCOUNT_ID \
     --repo leanprover/lean-eval-submissions --env cloudflare-staging
   gh secret set CLOUDFLARE_API_TOKEN \
     --repo leanprover/lean-eval-submissions --env cloudflare-staging
   gh secret set CLOUDFLARE_ACCOUNT_ID \
     --repo leanprover/lean-eval-submissions --env cloudflare-production
   gh secret set CLOUDFLARE_API_TOKEN \
     --repo leanprover/lean-eval-submissions --env cloudflare-production
   ```

6. Merge the reviewed Worker PR with `INTAKE_ENABLED=false`. The workflow
   automatically deploys staging, validates its commit/environment/health body,
   and then deploys production from the same commit.

7. Set distinct runtime secrets interactively after the Workers exist:

   ```bash
   cd server
   npx wrangler secret put GITHUB_STATE_TOKEN --env staging
   npx wrangler secret put READINESS_TOKEN --env staging
   npx wrangler secret put GITHUB_STATE_TOKEN --env production
   npx wrangler secret put READINESS_TOKEN --env production
   ```

   Also generate a different 32-byte-or-longer random `AUTH_TOKEN_SECRET` for
   each environment and enter it with `wrangler secret put`. Never reuse it as
   a GitHub, Cloudflare, or readiness credential.

8. Create two GitHub OAuth Apps owned by the organization, one for staging and
   one for production. Set their callback URLs exactly to the two HTTPS
   `/api/v1/oauth/callback` URLs in `wrangler.jsonc`; do not configure wildcard
   callbacks. Record the non-secret client IDs in `INFRASTRUCTURE.md`, then set
   each client ID and client secret interactively as environment-specific
   Worker secrets. Verify one successful staging login; the failure cases are
   covered by the automated Worker tests.

9. Create the source-verification and dispatch GitHub Apps with only the
   recorded permissions and installations. Deploy the implemented broker
   separately, bind only the matching staging/production service identity, and
   leave the two local static-token hooks absent in the Worker. The broker's
   exact-operation and repository allowlists are covered by automated tests.

10. Verify secret names without printing values. After each State token is
    approved, run `verify-state-writer.yml` for its environment from protected
    `main`; the authenticated same-commit ref update proves its ruleset bypass
    without enabling intake or changing State. Record the result, account, and
    deployment version IDs in `INFRASTRUCTURE.md`.

## Results migration walkthrough

After the results schema version 2 tooling PR is merged:

1. Run `migrate-results-v2.yml` with `apply=false`.
2. Download and retain the report artifact.
3. Confirm `ready_to_apply=true`, no duplicate IDs, exact source and migrated
   counts, the current source commit, and the canonical output digest.
4. Record approval of those exact values in the tracker.
5. Run the workflow with `apply=true` and the three expected values copied from
   that fresh report.
6. Confirm every live results file uses schema version 2, the writer lock was
   removed, queued record jobs completed, and schema versions 1 and 2 leaderboard
   projections still match.
7. If apply fails after acquiring the lock, leave it fail-closed. Review the
   run and use `resume_locked_migration` only with a new report; never delete the
   lock merely to unblock writers.

## Immediate rollout order

1. Resolve D1, D2, D4, D5, D8, D9, and D10.
2. Authorize repository creation, initial pushes, and tracker creation.
3. Publish the generator first; replace the local consumer pin with its exact
   SHA and reverify parity.
4. Publish State, staging State, and disabled releases tooling; configure State
   rulesets.
5. Publish draft PRs for catalog, results schema version 2/Worker,
   lifecycle-aware leaderboard preview, and
   software-verification statements.
6. Create Cloudflare GitHub environments and credentials.
7. Merge/deploy the intake-disabled Worker, verify staging/production health,
   and update the infrastructure ledger.
8. Approve and execute the fresh results schema version 2 migration.
9. Freeze the maintainer-approved v1 set.
10. Continue OAuth/agent intake, wire it to the implemented UUIDv7 archive
    writer/State locator, and complete replay implementation in staging;
    do not enable production intake or releases until D6 and the documented
    implementation gates are complete.
