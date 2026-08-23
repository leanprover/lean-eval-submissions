# [lifecycle overhaul] implementation tracker

Tracks implementation and production rollout of the plan in
[lean-eval#536](https://github.com/leanprover/lean-eval/pull/536) and the
[public implementation program](https://gist.github.com/kim-em/cd6ac1c049f459ef9aa37d6cf551d9e4).

Terminology: unqualified **v1** and **v2** name problem sets. The platform
work is the **lifecycle overhaul**; machine-format versions are always
qualified as **schema version 2**. Frozen identifiers and filenames remain
unchanged.

The detailed decision and operator procedures live in
`docs/overhaul-rollout-runbook.md`. Use this single tracker rather than opening
one issue per lane.

## Decisions

- [x] D1: keep raw production and staging State private and publish a
      reproducible, schema-validated public projection.
- [x] D2: mechanical base rule selected: visible formalization-evaluation
      problems with fewer than three accepted solves and no public submission
      (118 at the audited snapshot). Ten later merged problems were explicitly
      approved as additions; the final frozen v1 set has 128 members.
- [x] D3: approve both software-verification statements; retain both problems,
      apply the researched title/prose/citation corrections, and add the three
      agreed anti-vacuity requirements before publication.
- [x] D4: use isolated temporary account `lean-eval`
      (`a46b90978a1c29cc4795f30677e7e4b8`) and exact
      `lean-eval.workers.dev` endpoints for the intake-disabled bootstrap;
      never reuse or rename the Palomar subdomain. Kim Morrison is
      administrator and temporary cost owner. A later organization-account or
      provider migration is supported by the stable contracts, not required
      for this bootstrap.
- [x] D5: bootstrap with separate Kim-owned fine-grained PATs for staging and
      production State, each single-repository scoped and at most 90 days;
      rotate at least 14 days before expiry. The bootstrap is approved for
      initial staging and production intake once each token and its narrowly
      named ruleset bypass pass an environment-specific write test.
- [x] D6: approve key lifecycle, one-submission capability, contributor
      acknowledgement, and license wording before private intake/release. D6a
      selects a new dedicated AWS account with AWS KMS behind a
      provider-neutral wrap/unwrap adapter; migration rewraps identities without
      changing archives or stable IDs. D6b accepts no provider-loss recovery.
      The capability implementation is delegated to the implementation review;
      concise contributor/release wording is approved.
- [x] D7: approve fresh dry run `32569220655` at source `ddc0e4ec`, source
      digest `884c3837`, 1,298 records, and output digest `b78fb207`; apply run
      `32569936026` completed as `c3491661` and independent validation passed.
- [x] D8: acknowledge UUID-prefix State paths and the durable Git writer lock
      as correctness-preserving deviations from the literal program text.
- [x] D9: use two least-privilege GitHub Apps behind an organization-operated
      token broker; implementation identifiers are recorded when provisioned.
- [x] D10: use a two-UTC-calendar-month embargo and initially plan a four-week
      issue-intake overlap. A later explicit maintainer decision may shorten
      the issue overlap and must be announced before closure.

## Repository and contract foundations

- [x] Create and publish `lean-eval-generator`.
- [x] Create and publish production State with the D1 visibility.
- [x] Create and publish staging State with the D1 visibility.
- [x] Create and publish disabled `lean-eval-releases` tooling.
- [x] Replace LeanEval's local generator path with an exact public commit SHA.
- [x] Verify generator byte parity after the remote pin (theorem- and
      definition-hole fixtures, 10/10 files each).
- [x] Merge generator fixes `lean-eval-generator#1` / `#2` and synchronization
      PR `#3`, advance exact pins, rerun corpus parity, and merge the
      embedded-core removal in `lean-eval#553`. All three generator PRs merged
      into exact main commit `77373a53`; consumer `#553` merged as
      `b91d4757`, after local parity/build/test checks and hosted run
      `32559642804` passed all eight catalog shards, the inventory aggregate,
      repository checks, and security/scoring smoke tests.
- [x] Configure State rulesets and append-only validation.
- [x] Publish the schema-validated public projection of private production
      State without exposing private source or operational metadata
      (`lean-eval-state#4`; staging mirror `lean-eval-state-staging#4`).
- [x] Complete lifecycle-aware leaderboard consumption and publish the exact
      validated projection (`lean-eval-leaderboard#72`, merge `dd5d508d`; Pages
      deployment run `32557778003`).

## Existing-repository PRs

- [x] Open catalog lifecycle/tags/audit PR.
- [x] Open results schema version 2 compatibility and migration-tooling PR.
- [x] Open intake-disabled Worker, deployment, rollback, threat-model, and
      infrastructure-ledger PR.
- [x] Open lifecycle-aware leaderboard preview PR.
- [x] Open software-verification draft PR after D3 review.
- [x] Open LeanEval generator-consumer PR after the public generator pin exists.
- [x] Open independent-kernel shadow smoke `lean-eval-submissions#1207` and
      AWS one-submission key-adapter staging smoke `#1208`; both merged and
      deployed intake-disabled as exact commit `a34b2053`.
- [x] Provision dedicated AWS account `161072922960`, exact GitHub OIDC
      provider, and isolated staging/production key-adapter stacks. Only the
      staging wrap and replay-invoker role variables are connected; production
      remains dormant and intake-disabled.
- [x] Pass the synthetic staging key-adapter round trip from protected immutable
      commit `d487c9d5` (run `32568604230`): Encrypt-only wrap, source-free
      handoff, Invoke-only unwrap, second-use rejection, authority removal, and
      local decryption. This is not a private replay/backend demonstration.
- [x] Merge disabled release terminology `lean-eval-releases#3`, planner `#1`,
      and reconstruction smoke `#2`; final main is `f1f83344`, all hosted
      validation is green, and publication remains disabled.

## Cloudflare bootstrap

- [x] Create protected `cloudflare-staging` and `cloudflare-production` GitHub
      environments.
- [x] Install the Cloudflare account ID in both environments.
- [x] Install distinct scoped deployment API tokens in those environments.
- [x] Add Containers: Edit to both existing account-scoped deployment tokens;
      operator-confirmed 2026-08-22, retaining Workers Scripts: Edit and no
      other account or zone permission. Live verification remains part of the
      first staging container deployment.
- [x] Create the protected `submission-dispatch-promotion` environment and the
      immutable `lean-eval-dispatch/*` tag ruleset.
- [x] Install distinct random `AUTH_TOKEN_SECRET` and `READINESS_TOKEN` values.
- [x] Create distinct staging/production GitHub OAuth Apps with exact callback
      URLs and install their credentials.
- [x] Deploy staging manually with intake disabled and record the Worker,
      `workers.dev` endpoint, account, subdomain, and version identifiers.
- [x] Promote the same commit to production and verify the structured health
      response.
- [x] Install distinct readiness secrets in each Worker.
- [x] Obtain organization approval for the installed, distinct State-writer
      credentials and test each with its matching ruleset bypass (preflight
      runs `32465890236` and `32465892118`).
- [x] Implement the D9 private service-binding broker and separate source and
      dispatch authority paths.
- [x] Deploy both private brokers and bind the intake-disabled staging and
      production Workers to them.
- [x] Create both Apps, install the dispatcher on only the submissions
      repository, and provision both broker environments; keep the local static
      verification/dispatch-token hooks absent in production.
- [x] Complete the ownership transfers of both broker GitHub Apps to
      `leanprover`; public records and protected preflights verify unchanged
      App IDs, working private keys, and dispatcher installation `155329316`.
- [x] Replace the credentialed private-gist assumption with anonymous exact-ID
      verification of GitHub's unlisted secret gist, while retaining exact
      owner/content/visibility/truncation checks and keeping the broker closed
      to `/gists/`.

## Data and product rollout

- [x] Produce a fresh results schema version 2 dry-run artifact.
- [x] Review and approve dry run `32569220655` and its exact source commit,
      source digest, 1,298-record count, and canonical output digest (D7).
- [x] Execute authorized run `32569936026`; main `c3491661` contains 44/44
      schema-version-2 files, the writer lock is absent, no record was lost or
      duplicated, no submission writer is queued, and main CI is green.
- [x] Define and audit the approved mechanical 118-member v1 base set.
- [x] Freeze the v1 set when its reviewed PR merges (`lean-eval#540`,
      merge commit `547b00ed345bc0737dd94847d6b67cb681b6178a`).
- [x] Add the ten explicitly approved post-audit problems in `lean-eval#548`;
      final v1 membership is 128 at merge `21c6c021`.
- [x] Complete staging OAuth/agent intake and exact-ref dispatch. Hosted run
      `32546606639` archived the exact private fixture, produced the deliberate
      rejection, and recorded archive completion plus evaluation start/reject
      events through staging State commit `b2160515`; no accepted result was
      written.
- [x] Demonstrate credential-free historical public replay (hosted run
      `32499490261`, workflow commit `757b0831`).
- [x] Demonstrate isolated private replay. Protected deployment run
      `32573880099` and source-free acceptance run `32574078784` prove the
      12 GiB, max-one-instance, SSH-off, network-disabled Cloudflare Sandbox,
      exact GitHub OIDC boundary, fixed-command decrypt, and destruction. The
      provider-neutral contract and automatic deployment are live; general and
      production replay remain disabled.
- [x] Verify synthetic single-archive decrypt, second-use and wrong-archive
      refusal, blocked egress, and sandbox destruction before enabling private
      replay. Evidence is recorded in `INFRASTRUCTURE.md`.
- [x] Prove the same boundary with a real accepted schema-version-3 staging
      archive. Immutable-tag run `32618166048` selected submission
      `01a02cb4-5e7c-7fb3-a4ab-b6fabbb72584` from State, consumed and refused
      reuse of its exact KMS capability, verified the plaintext digest and safe
      archive shape only inside the network-disabled Sandbox, and confirmed
      destruction without any write authority. Authoritative queue execution
      and production replay remain disabled.
- [x] Demonstrate release reconstruction separately without enabling release
      or publication. Protected `lean-eval-releases` run `32574614106` at exact
      main commit `f1f83344` planned one due synthetic release, reconstructed
      and validated its manifest, enforced the public-file allowlist, excluded
      a private fixture file, and left Git and State unchanged.
- [ ] Enable production intake and begin the four-week issue-intake adoption
      window.
- [x] Complete leaderboard preview review and cut over with rollback retained
      (`lean-eval-leaderboard#72`; Pages run `32557778003`).
- [ ] Close issue intake only after the time, incident, submitter, adoption, and
      announcement gates in the implementation program pass.

## External coordination

- [x] Coordinate the published generator contract through lean-eval#533 and
      formal-conjectures#4951.
- [x] Close the whole-FC100 extraction findings in the FC-owned importer:
      current `formal-conjectures#4951` imports, verifies, classifies, and
      generates all 100 declarations (92 research-open and 8 research-solved).
      Its pinned baseline builds 97/100 at LeanEval pins with three exact known
      failures; generator PRs `#1` / `#2` retire Erdos125, and the independently
      verified FC toolchain bump retires the remaining two.
- [x] Reproduce the FC100 dependency audit before import: 100/100 declarations
      resolved, 280 closure edges, no target-to-target dependency, and one
      fail-closed `Erdos324.erdos_324.match_1` orphan recorded upstream.
- [x] Obtain a compatible FC-owned importer/output contract with strict,
      deterministic provenance (`formal-conjectures#4951`, green at head
      `2654e42de2026de6cdb248ad5ed0f1c7d659c8fa`).
- [ ] Launch FC100/open-conjectures only after generator pins advance, the
      importer receives FC maintainer review, and the production launch gates
      are satisfied.
