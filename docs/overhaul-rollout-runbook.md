# LeanEval overhaul rollout runbook

This runbook separates work that is technically ready from decisions and
credential operations that require a maintainer. It supplements
[`INFRASTRUCTURE.md`](../INFRASTRUCTURE.md); the infrastructure ledger remains
the source of truth after resources are created.

Do not put token values, private keys, recovery material, or OAuth client
secrets in this file, an issue, a pull request, or a terminal transcript.

## Current implementation checkpoint

The local implementation is committed in isolated, clean worktrees:

| Repository / lane | Commit |
| --- | --- |
| `lean-eval`, catalog | `2c4dc5395366306b24465a56f375d891ab599f8d` |
| `lean-eval`, software-verification drafts | `7a85e1c9e1f10b963fab400018b0463e95ce64f4` |
| `lean-eval`, extracted-generator consumer | `6168f7bcc1a3cf5bd5a2d7b776cc054b02fd9140` |
| `lean-eval`, combined local integration | `24d5d9d` |
| `lean-eval-generator` | `a726789` |
| `lean-eval-submissions`, results v2 | `530925bb29e90123ba6052e81654091445e45c42` |
| `lean-eval-submissions`, integrated Worker | `ab722e1` plus this runbook |
| production State | `8699eeddb248137ca33e002019554d61923528ca` |
| staging State | `841911ef662d3856d254e33459d83bed3143d5b2` |
| releases | `3edc8dcd7dfebf8a3c649d32755437ad2087b9d0` |
| leaderboard preview | `a21a9cd438c3ffaadbd04d5166a83713ca224ac5` |

These are local commit identifiers until their repositories or branches are
published. No live results migration or Cloudflare deployment has occurred.

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

### D4: Cloudflare ownership

Wrangler is currently authenticated to account
`d789bf36d237e0cb313be59b927c82bd` (`Kim@lean-fro.org's Account`). That does
not establish ownership of the `lean-lang.org` zone.

Recommended choice: deploy from an organization-controlled Cloudflare account
that owns `lean-lang.org`, with named primary and backup administrators and a
documented cost owner. Do not deploy these production hostnames into a personal
account.

Decision to record: Cloudflare account name and ID, zone ID, primary and backup
administrators, and cost owner.

### D5: initial State writer credential

The implemented bootstrap uses separate organization-owned, fine-grained PATs
because a static GitHub App installation token expires. Each token is limited
to one State repository, Contents read/write and Metadata read, and at most a
90-day lifetime.

- **Fine-grained PAT bootstrap (recommended for intake-disabled rollout):**
  operationally simple, with a mandatory rotation date and machine owner.
- **GitHub App before bootstrap:** requires implementing and reviewing App JWT
  signing and installation-token refresh or a token broker first.

Decision to record: credential model, machine owner for each environment,
expiration, rotation owner, and whether production intake is forbidden until
the GitHub App replacement exists.

### D6: publication, key recovery, and license policy

Before replay, private-source intake, or automatic releases are enabled, humans
must select and approve:

- the KMS/HSM or equivalent root and per-submission envelope format;
- two-maintainer recovery and rotation responsibility;
- the one-use replay/release capability issuer;
- the exact contributor acknowledgement and Apache-2.0 release wording;
- the emergency purge and release-history cleanup authority.

The release repository and validator may be published while publication stays
disabled. These choices are not needed to deploy a health-only Worker with
`INTAKE_ENABLED=false`.

### D7: live results-v2 migration

The latest local dry run preserved 1,279 of 1,279 records at submissions commit
`9d3fa0a138aa5bb667669b6c10dbe0c2d101b2bc`, with no duplicate IDs,
source digest
`8472888c040a40acfaaa2e596b3acd6a1ebfe62f4effdb2e519c17acf4f16e8c`, and
output digest
`30dd95781809d13791f0de2d5bd935b4fc77c4fac28d26a23373e370e878d988`.
That evidence becomes stale if `main` changes.

Authorization must name the fresh workflow run and approve its exact source
commit, record count, and output digest. `apply=true` is a separate decision
from merging the migration tooling. After the first v2-only record lands,
repairs are forward-only.

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
4. Enable secret scanning, dependency alerts, and audit-log review.
5. Configure an off-platform mirror/backup and record its destination in
   `INFRASTRUCTURE.md`.
6. Run the repository validator and one restore drill before enabling intake.

Create each fine-grained PAT in GitHub's UI so its value never appears in a
command history or transcript. Select only its State repository, Metadata read,
and Contents read/write. Record owner, creation, expiry, and rotation dates—but
not the value—in `INFRASTRUCTURE.md`.

## Cloudflare and GitHub environment walkthrough

These steps require the account selected in D4 and the two scoped deployment
tokens. Cloudflare Custom Domains create the DNS records and certificates for
the exact hostnames declared in `server/wrangler.jsonc`; verify no conflicting
CNAME exists first.

1. Ensure the operator is a member of the correct Cloudflare account, then run:

   ```bash
   cd server
   npx wrangler whoami --json
   ```

   Stop unless the selected account ID is present.

2. In the Cloudflare dashboard, create distinct staging and production API
   tokens. Restrict them to the selected account, Workers Scripts edit, and
   only the zone/route permissions needed for their respective custom domain.
   Do not reuse a personal global API key.

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

4. Add `CLOUDFLARE_ACCOUNT_ID` and the environment-specific
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

5. Merge the reviewed Worker PR with `INTAKE_ENABLED=false`. The workflow
   automatically deploys staging, validates its commit/environment/health body,
   and then deploys production from the same commit.

6. Set distinct runtime secrets interactively after the Workers exist:

   ```bash
   cd server
   npx wrangler secret put GITHUB_STATE_TOKEN --env staging
   npx wrangler secret put READINESS_TOKEN --env staging
   npx wrangler secret put GITHUB_STATE_TOKEN --env production
   npx wrangler secret put READINESS_TOKEN --env production
   ```

7. Verify secret names without printing values, exercise authenticated
   readiness, record account/zone IDs and the deployment version IDs in
   `INFRASTRUCTURE.md`, and run the guarded rollback workflow once while intake
   remains disabled.

## Results migration walkthrough

After the results-v2 tooling PR is merged:

1. Run `migrate-results-v2.yml` with `apply=false`.
2. Download and retain the report artifact.
3. Confirm `ready_to_apply=true`, no duplicate IDs, exact source and migrated
   counts, the current source commit, and the canonical output digest.
4. Record approval of those exact values in the tracker.
5. Run the workflow with `apply=true` and the three expected values copied from
   that fresh report.
6. Confirm every live results file is schema v2, the writer lock was removed,
   queued record jobs completed, and v1/v2 leaderboard projections still
   match.
7. If apply fails after acquiring the lock, leave it fail-closed. Review the
   run and use `resume_locked_migration` only with a new report; never delete the
   lock merely to unblock writers.

## Immediate rollout order

1. Resolve D1, D2, D4, and D5.
2. Authorize repository creation, initial pushes, and tracker creation.
3. Publish the generator first; replace the local consumer pin with its exact
   SHA and reverify parity.
4. Publish State, staging State, and disabled releases tooling; configure State
   rulesets and backups.
5. Publish draft PRs for catalog, results-v2/Worker, leaderboard preview, and
   software-verification statements.
6. Create Cloudflare GitHub environments and credentials.
7. Merge/deploy the intake-disabled Worker, run staging/production health and
   rollback drills, and update the infrastructure ledger.
8. Approve and execute the fresh results-v2 migration.
9. Freeze the maintainer-approved v1 set.
10. Continue OAuth/agent intake and archive/replay implementation in staging;
    do not enable production intake or releases until D6 and the documented
    security drills are complete.
