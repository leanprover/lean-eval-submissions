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
| production State | `a53c658a2de2188675134dc2890285fbaa17cf5a` on private protected `main`; hardened historical-public validation, release-status v2, and permanent effective-result reservation contracts are merged, and the graph still contains only `system.initialized`, with no accepted submission, reservation, or due release work; exact-main validator run `32772040095` passed |
| staging State | reviewed contract `48f8c975d725a9ac18df545653fdb2f8371c3293`; current private protected `main` is `dbe3a323efdc51c08079d75ef826ff1a936e9946` after the `08bf2c8e` promotion canary and preserves the exact reviewed README/docs/schema/scripts proof entries; contract validation run `32772193134` passed |
| `lean-eval-releases` | `57ab36341ccf653b45366c32d4472b9ee670890b` on `main`; source-free recovery `#8`, State-bound removal planning `#9`, and the deterministic automatic controller `#10` are merged; exact-main validation `32719159678` and publication-disabled Git credential preflight `32723471497` passed; credentialed staging unwrap and publication remain disabled |
| catalog, generator consumer, software verification | v1 freeze merged as `lean-eval#540`; final 128-member v1 set merged in `#548`; terminology rule merged in `#554`; standalone-generator consumer merged in `#553`; current main `b91d4757aa0d7776c02540c9089df54fa0d0658a` |
| results schema version 2, intake server, replay contracts | schema-version-3 per-submission archive lane `#1250`, accepted result lifecycle `#1251`, guarded historical migration `#1252`, private replay planning/schema alignment `#1253` / `#1254`, accepted-archive staging boundary `#1255`, and immutable release OIDC trust `#1256` are merged; exact runtime `08bf2c8ef2a9fbbb4f10dc0432969ba11c29bc40` is deployed intake- and replay-disabled after fail-closed rollout `32772828260`; its State-contract repin is qualified but not yet deployed |
| AWS archive-key custody | dedicated account `lean-eval` (`161072922960`) and isolated stacks are provisioned; accepted-archive staging run `32618166048` passed. Release OIDC template correction is merged but the live stacks still require an authenticated operator update: release staging runs `32617539355` and `32624640050` failed at STS before Lambda or decrypt. Production intake archive/replay roles remain disconnected |
| lifecycle-aware leaderboard | preview foundation merged as `lean-eval-leaderboard#69`; UI terminology merged in `#73`; deeper schema terminology merged in `#74`; cutover `#72` is merged and live at `https://lean-lang.org/eval/`, with `/legacy/` retained and read-only State deploy key `160968617` provisioned; owner-scoped State v4 model-identity consumption merged as `#75` (`89be802f`) after exact-head run `32741897578`, and production Pages run `32747172862` deployed it successfully |

The private broker, replay, and intake Workers are deployed in staging and
production from exact commit `08bf2c8ef2a9fbbb4f10dc0432969ba11c29bc40`.
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
version identifiers are recorded in `INFRASTRUCTURE.md`. D7 is complete: fresh
dry run `32569220655` was approved and apply run `32569936026` migrated all
1,298 records and 44 files to schema version 2 as main `c3491661`. The durable
writer lock is absent, no submission writer is queued, an independent rerun
finds zero changes and the approved `b78fb207…` canonical output digest, and
main CI `32569954466` passed.

The dedicated AWS account now holds the KMS keys, conditional one-use tables,
and direct-Lambda unwrap gates described by D6. This is archive-key custody,
not an AWS evaluation backend. Production role variables remain unset.
Cloudflare Sandbox is the selected provider-neutral disposable executor;
protected deployment `32573880099` and synthetic acceptance `32574078784`
proved its 12 GiB staging boundary, wrong-archive and reuse refusal, blocked
egress, fixed-command decrypt, and destruction without writing State. General
and production replay remain disabled.

The live server archive lane now creates a fresh provider-neutral KMS envelope
for every submission before evaluation. Two accepted staging submissions have
completed this lane. Deployment run `32617911271` published exact commit
`12da2fa504ea4b9408d9fb24773886df02e20d66` with the approved 12 GiB ceiling,
and immutable-tag run `32618166048` passed the real accepted-archive boundary.
The authoritative background-protocol image and State queue path have since
been qualified separately; general and production replay remain disabled. The
accepted-archive workflow itself attests only recovery and destruction.

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
direct-Lambda unwrap, and conditional DynamoDB consume boundary. Staging wrap,
staging replay, and both release environments have role variables; production
archive and replay remain disconnected. Release publication remains disabled
because the repository variable `PUBLICATION_ENABLED` is absent. The
Cloudflare Sandbox backend is provisioned with general replay disabled. The
reviewed production ceiling is one 12 GiB `standard-4` instance; authoritative
queue consumption and the remaining isolation evidence are separate launch
gates.

The release controller is merged through exact `lean-eval-releases` commit
`57ab36341ccf653b45366c32d4472b9ee670890b`; protected main validation run
`32719159678` passed. Protected production credential preflight `32723471497`
bound that exact controller to production State
`0c8759946df0da1338a0c73bf5bd75d182038286`, validated its sole immutable
initialization event, materialized all six deterministic views, and passed
exact-ref no-op dry-run pushes with both production write keys while the
publication variable remained absent. This establishes current Git credential
authentication only. It made no actual ref update and did not exercise the
audit key, OIDC, AWS, Lambda, a one-use capability, archive decryption or
reconstruction, State callback/recovery, artifact upload, or publication.

Before publication can be enabled, apply and verify the reviewed live release
OIDC trust correction, then pass `Prove one credentialed staging release
unwrap` against one accepted staging release. Both existing attempts,
`32617539355` and `32624640050`, stopped at STS role assumption before Lambda
or decrypt. Record that successful staging evidence and complete the explicit
launch review before deliberately creating repository variable
`PUBLICATION_ENABLED=true`. Production State currently has no accepted
submission or due release work, so the controller would initially be inert;
the first later due release will still be the first production exercise of the
audit/decrypt and real release/State push paths.

**Contributor acknowledgement approved 2026-08-20:** “By submitting, I confirm
that I have authority to provide this source. I authorize Lean Eval to store
and run it privately for evaluation, publish evaluation metadata and results,
and, two UTC calendar months after acceptance, publish the submitted source
under the Apache License 2.0. I will not submit secrets or material I am not
authorized to disclose.” Keep this acknowledgement adjacent to the submit
action rather than expanding it into a separate policy questionnaire.

### D7: live results schema version 2 migration

**Completed 2026-08-22.** Fresh dry run `32569220655` preserved 1,298 of 1,298
records across 44 files at source commit
`ddc0e4ec8980296a5312844dedd5513d1d604e5b`, with source digest
`884c38373f8ecafbbc3894a6cb90cdca476f558bb32fe44d0af08e8c62fd2e05`, no
duplicate result IDs, and canonical output digest
`b78fb207d4711c2f59970fd3e769c483cf7eab8f5afb1fec07abe7cadbfc24c4`.
The maintainer approved those exact values. Apply run `32569936026` used the
durable lock and produced `c3491661da9dcdad908d1b1e78576d9f64f112f4`;
post-apply verification found 44/44 schema-version-2 files, the same record
count and output digest, no lock, no queued writer, and zero remaining changes.
Repairs are now forward-only.

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

### Result-amendment collision-index gate

The permanent historical-reservation policy is selected and implemented.
State defines `eri1_<sha256>` over the canonical
`[owner_login, declared_model, problem_id, statement_revision]` tuple and stores
one closed reservation under `views/effective-result-identities/`. Once a tuple
belongs to a stable result it is never deleted or rebound to another result;
the same result may revisit it.

Production contract `a53c658a2de2188675134dc2890285fbaa17cf5a` validates the
empty production authority/reservation set. Staging contract
`48f8c975d725a9ac18df545653fdb2f8371c3293` materializes the two existing
base-tuple reservations and passed exact-contract validation run `32772193134`.
Recording or claiming creates/confirms the base reservation in the same CAS
transaction as authority and lifecycle views. Repair application target-reads
only the corrected reservation: absence creates it atomically with the event
and amendment view, the same result is an allowed historical revisit, and any
cross-result use permanently conflicts. Pending and rejected repairs never
reserve. The aggregate is no longer in online admission.

The Worker binds the complete repository-specific `README.md`, `docs`,
`schema`, and `scripts` subtrees through one non-recursive current-root-tree
response plus ancestry when the head has advanced. Changed, missing, duplicate,
or wrong-type entries fail closed. This cuts an uncached descendant contract
proof from 16 GitHub requests (comparison plus 15 contents reads) to 2 while
binding whole subtrees rather than selected blobs.

The maximum external-subrequest route is repair application under maximum CAS
contention: 28 requests for the initial State graph, 8 for protected Results and
benchmark ancestry, the twice-read exact Results blob, and two benchmark
manifests, plus 9 writer attempts at a conservative 37 requests each (including
the immutable reservation-provenance event on a same-result revisit), for a
closed bound of 369. This
includes uncached contract proofs, every schema-bounded historical reference,
candidate reservation read, tree/commit creation, duplicate ref-update retry,
and reachability check. Before any owner or maintainer gate is enabled, confirm
the deployed Workers plan allows at least 369 external subrequests per request;
the 50-subrequest free allowance is insufficient.

This gate closed on 2026-08-25 against disabled deployed commit
`d34aab279dd99380530b9d77c3aa199559849209`. Retry `32792905120` started at
State `2436d631a005b7f2d83e5385c4041c7f05259e0f`, durably applied the repair at
`9844e4b5d515810b90f0bf32bd25aba6aa0a7f9e`, and then failed closed on a
transient broker response. Exact successful run `32793103590` started at that
`9844e4b5` head, idempotently reverified the apply, completed request/reject,
and produced final State `cc52d7c298450df639a59ca9fff8914438626d12`.
The apply reservation exists, the reject candidate remains unreserved, and all
four fixed events exist. Intake, the owner API, and the maintainer API remained
disabled. The temporary credential was revoked immediately and the one-shot
route, workflow, fixture, binding, and required-secret declaration were then
retired. No full repository scan or aggregate may return to online collision
admission; enabling the public maintainer API remains a separate identity,
subrequest-budget, and rollout decision.

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
documented safety gates, and the backed-up leaderboard-branch repair. D7 was
separately approved against fresh report `32569220655` and applied by run
`32569936026`. Secret values and manual
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
   access; each promotion job narrows that token to `contents: write` and
   `actions: read`. Add a distinct 32-byte lowercase-hex
   `DISPATCH_PROMOTION_APPROVAL_GUARD` secret to this environment only (never
   at repository or organization scope). The value grants no API access; its
   sole purpose is to make a missing or accidentally auto-created unprotected
   environment fail closed before tag creation.

   Create an active tag ruleset whose target is
   `refs/tags/lean-eval-dispatch/*`. Allow tag creation, but block updates and
   deletion; do not grant the Worker, deployment token, dispatch broker, or
   ordinary maintainers a bypass. Record the ruleset ID and required-reviewer
   owners in `INFRASTRUCTURE.md`.

   Two path-partitioned minters enter that environment. `deploy-worker.yml`
   handles runtime-bound changes, including the staging intake and replay
   workflows whose exact-commit preconditions must match the live staging
   deployment. Shipping any of those workflow changes therefore performs the
   ordinary disabled staging/canary/production rollout and resets staging
   intake to the tracked disabled default.
   `promote-workflow-dispatch-ref.yml` handles the remaining tag-consuming
   operational workflows without invoking Wrangler. Each proves the commit is
   reachable from `main`, waits for the exact protected-main CI run, confirms
   that the commit contains the required dispatch workflows, then creates
   `lean-eval-dispatch/<GITHUB_SHA>`. If the tag already exists, the job succeeds
   only when it resolves to the same commit; this makes a same-SHA race harmless
   while a collision fails closed. The runtime minter passes the read-back tag
   as `DISPATCH_WORKFLOW_REF` to both Wrangler deployments. Missing approval,
   failed exact-main CI, insufficient token policy, missing ruleset setup,
   collision, or read-back mismatch stops promotion or deployment; intake
   remains false.

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
   automatically deploys staging and validates its exact
   commit/environment/health body. It then uses the staging environment's
   existing `READINESS_TOKEN` to run the promotion canary; no new canary secret
   or production authority exists. The request must bind the exact deployed
   commit and its immutable `lean-eval-dispatch/<commit>` tag.

   The canary source is the private synthetic fixture repository at immutable
   commit `ae38f4d3e4ad2991212135435f54e6640bcc89e7`, whose proof is deliberately
   rejected. The Worker verifies the repository through the real source-reader
   broker and atomically accepts one deterministic withheld staging submission
   and outbox for the exact deployment `GITHUB_RUN_ID` plus
   `GITHUB_RUN_ATTEMPT`. The first call records a fresh collision/retry proof;
   polls within that attempt report the exact already-recorded proof and are idempotent; a workflow
   rerun gets fresh State and contention material. Its fixed synthetic
   2026-08-20 timestamp window is only a deterministic identity input.

   From one current State snapshot, the adapter creates two source-free sibling
   commits, applies an empty-tree barrier commit by forward-only CAS, observes GitHub reject the other
   with 409/422, then rebuilds/retries the evidence atop the new head and
   verifies application. The barrier changes the commit object but not the
   validated State tree, so State's full-tree validation and append-only diff
   both accept it; adapter tests pin the unchanged base tree and non-forced ref
   update. The HTTP request does **not** dispatch. Every canary
   UUID ends in the fixed `ca` shard, and its synthetic identity and outbox ref
   retain the originating deployment commit, so the real one-minute Cron Trigger can
   discover and safely reconcile pending prior workflow attempts even after a
   Worker deployment changes. It strictly classifies and
   re-derives each identity, processes at most 20 due entries, contains each
   source-free error, terminally removes exact failed entries at the 32-attempt
   bound, and still runs ordinary staging reconciliation.

   Reconciliation uses the normal State-success path and actual dispatch broker
   but targets only the immutable-ref, permissionless
   `promotion-canary.yml` no-op. Under that exact dedicated target it never dispatches `submission.yml`, writes the
   shared audit archive, starts evaluation, writes Results, or schedules a
   release. The workflow polls the authenticated source-free response until the
   scheduled path records GitHub's acceptance of the exact workflow dispatch
   and removes the outbox. That `dispatch.status=succeeded` state is not evidence
   that the asynchronous no-op Actions job has completed.

   Missing authentication, a moved source identity/visibility, a non-exact
   deployment/tag binding, absent State authority, a CAS update that does not
   collide, broker failure, a malformed view/outbox, or a scheduler timeout
   fails closed. Retries reuse the same UUIDv7 and immutable evidence for the
   exact protected-main commit/run/attempt tuple. The production config explicitly sets
   `PROMOTION_CANARY_ENABLED=false`; `/healthz` separately exposes the
   source-free configured and effective false values so the production
   route-disable state is observable, while the route is hidden and its scheduled
   handler has no synthetic authority. Emergency rollback qualification also
   rejects any production target with this flag enabled, before mutation. Only after the staging job passes does
   the workflow deploy production from the same commit. Logs contain only the
   source-free status fields and never the readiness token, source fields, or
   upstream response bodies.

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

## Historical public GitHub evidence walkthrough

Treat GitHub evidence resolution as a source-free, immutable input to corpus
planning, not as replay execution or a permanent-unavailability decision.

1. Check out the reviewed protected-main commit and create its immutable
   `lean-eval-dispatch/<commit>` tag through the protected promotion workflow.
2. Generate the historical inventory and public resolution requests twice.
   Require byte-identical output, then record both SHA-256 digests, the
   workflow-definition registry digest, request/result counts, and canonical
   results-store digest.
3. Dispatch every shard for one exact shard count from that immutable tag.
   Schedule shards across rate-limit windows rather than using a parallel
   matrix. Confirm each run's head commit, attempt, success conclusion, artifact
   name, artifact ID, and package digest with the Actions API.
4. Download every sanitized shard artifact before retention expires and run
   `scripts/aggregate_public_replay_github_evidence.py` offline from the same
   clean source commit. Supply every shard exactly once and validate the output
   against both the runtime validator and the published aggregate schema.
5. Commit the sub-1-MiB source-free aggregate as a new immutable evidence
   object. Record its exact byte digest, all shard JSON digests, run and artifact
   IDs, input digests, classification counts, and artifact expiry date in a
   linked evidence note and `INFRASTRUCTURE.md`. Do not rely on the 30-day
   Actions artifacts as the durable record, and do not overwrite an older
   aggregate when a probe or adjudication is rerun.
6. Join classifications to the exact request artifact to report both request
   and accepted-result counts. Only `resolved` groups may advance to exact-pin
   replay. Keep `source_unavailable`, indeterminate, ambiguous, unreviewed, and
   missing-evidence groups pending until their separately reviewed next action.

The first complete post-registry pass followed this procedure at source
`5746f90e72e863d96d992938aea0609978d1560c`. Runs `32718053904` through
`32719340876` covered 315 requests / 633 results; 69 / 135 resolved and 246 /
498 remain pending. The exact digests, classifications, artifact IDs, expiry,
and permanent aggregate are recorded in
[`historical-public-evidence-rerun.md`](historical-public-evidence-rerun.md).

The token-free public-Gist rerun followed the same procedure at source
`6c13c245d17a1e25a59846769e533265e8ac9ba8`. Sixteen sequential retained runs
`32768996061` through `32770548866` covered the same 315 requests / 633 results;
126 / 192 are resolved and 189 / 441 remain pending, with zero source-probe or
generic-probe indeterminacy. Exact inputs, classifications, artifact IDs,
digests, expiry, and the permanent aggregate are recorded in
[`historical-public-gist-probe-rerun.md`](historical-public-gist-probe-rerun.md).

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

## Production intake toggle

Production intake has no out-of-band or manual toggle. After all launch gates
are complete and their exact evidence is recorded, prepare one reviewed PR that
changes only `env.production.vars.INTAKE_ENABLED` in
`server/wrangler.jsonc` from the tracked string `"false"` to `"true"`.
Regenerate the checked-in Wrangler types and update the focused production
expectation in `tests/test_worker_intake_configuration.py`, but make no other
semantic change.
Do not change staging's tracked `"false"` value or the staging-only manual
workflow. The protected main deployment must deploy that exact configuration
through staging and production. Production is first provisioned and verified at
the exact merge commit with intake forced false; the exact broker, disabled
replay, and protected State dependencies are then qualified. Before any
effective enabled mutation, the controller creates a one-use request bound to
its exact commit, run ID and attempt, target commit, production environment,
and reviewed State commit. The first enabled deployment is a Worker-enforced
lease of at most fifteen minutes. Every intake request checks the lease at
admission and again immediately before State acceptance, failing closed once
the lease expires even if every Actions runner disappears. The
controller must observe 100% traffic, exact merge-commit configured/effective
leased health, consume the request nonce by exact-head State CAS, and prove
protected State is unchanged. Only then may it deploy the same code in durable
mode; that durable deployment is the final workflow step and no risky
verification follows it. Begin the four-week issue-intake overlap only after
this controller succeeds. The ordinary health monitor compares against tracked
durable configuration and will therefore alert during the intentional leased
window; correlate that alert with the protected controller run. If recovery is
needed, inspect the dispatch outbox for an accepted request whose inline
dispatch failed. If public health is unreadable, recovery refuses to change an
unproven deployment and an operator must investigate or invoke the reviewed
manual disable path.
An ordinary deployment also restores staging to its tracked disabled state, so
review whether any temporary staging intake window must be resumed afterward.

For rollback, select reviewed intake, broker, and replay versions from one exact
immutable dispatch-tag commit. The rollback workflow checks the selected
intake version's `INTAKE_ENABLED` binding against that commit before mutation.
It deploys the target intake code with a temporary disabled override, restores
and verifies the private broker and replay/container dependencies while intake
is paused, and re-checks protected State. Rollback is disable-only, including
when the target commit tracks durable intake: it leaves the exact target code
forced to `INTAKE_ENABLED=false`/`disabled` and verifies the active version and
health. Durable enablement can return only through a later ordinary
protected-main rollout that repeats the finite-lease proof. Do not edit State
to imitate the rolled-back Cloudflare unit.

Before launch, confirm `intake-disable-recovery.yml` is enabled on protected
`main` and `cloudflare-production` admits its disable-only job. This workflow is
cleanup, not the safety boundary: it derives the exact protected controller
identity (failed automatically, or latest completed for a manual emergency
disable), accepts no manual pairing fields, contains no enabled deployment, and
requires live public health to prove production still runs that exact
controller commit with intake configured on before it mutates anything. A
staging-only failure or superseded controller is therefore a no-op. It verifies
the exact disabled version and health. GitHub may cancel an older pending run in
the shared serialization group; a queued, cancelled, or failed recovery cannot
extend the Worker lease. Investigate or rerun recovery before a later
production change, but never treat Actions availability as the expiry
mechanism.
If the terminal durable deploy succeeds at Cloudflare but its controller loses
the response or is cancelled before GitHub records success, the automatic
disable-only recovery deliberately returns intake to disabled. Treat that as a
safe interrupted launch, confirm the recovery run and public health, and rerun
the ordinary protected-main controller rather than enabling out of band.

Credential rotation and ruleset-bypass verification remain available after
launch through the protected `Verify State writer` workflow. Its authenticated
preflight requires the exact ready status, selected environment, and canonical
State commit, then accepts and reports only a boolean live intake state. It does
not require intake to be disabled, so it also remains valid while staging is
temporarily enabled through its manual workflow.
