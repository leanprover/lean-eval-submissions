# [overhaul] implementation tracker

Tracks implementation and production rollout of the plan in
[lean-eval#536](https://github.com/leanprover/lean-eval/pull/536) and the
[public implementation program](https://gist.github.com/kim-em/cd6ac1c049f459ef9aa37d6cf551d9e4).

The detailed decision and operator procedures live in
`docs/overhaul-rollout-runbook.md`. Use this single tracker rather than opening
one issue per lane.

## Decisions

- [x] D1: keep raw production and staging State private and publish a
      reproducible, schema-validated public projection.
- [x] D2: mechanical rule selected: visible formalization-evaluation problems
      with fewer than three accepted solves and no public submission (118 at
      the audited snapshot); use a fresh pre-merge audit, documented exclusions
      only, no additions, and freeze irreversibly when the reviewed PR merges.
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
- [ ] D7: approve a fresh results-v2 migration report's exact source commit,
      record count, and output digest.
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
- [x] Configure State rulesets and append-only validation.

## Existing-repository PRs

- [x] Open catalog lifecycle/tags/audit PR.
- [x] Open results-v2 compatibility and migration-tooling PR.
- [x] Open intake-disabled Worker, deployment, rollback, threat-model, and
      infrastructure-ledger PR.
- [x] Open leaderboard results-v2 preview PR.
- [x] Open software-verification draft PR after D3 review.
- [x] Open LeanEval generator-consumer PR after the public generator pin exists.

## Cloudflare bootstrap

- [x] Create protected `cloudflare-staging` and `cloudflare-production` GitHub
      environments.
- [x] Install the Cloudflare account ID in both environments.
- [x] Install distinct scoped deployment API tokens in those environments.
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
- [ ] Obtain organization approval for the installed, distinct State-writer
      credentials and test each with its matching ruleset bypass.
- [x] Implement the D9 private service-binding broker and separate source and
      dispatch authority paths.
- [x] Deploy both private brokers and bind the intake-disabled staging and
      production Workers to them.
- [x] Create both Apps, install the dispatcher on only the submissions
      repository, and provision both broker environments; keep the local static
      verification/dispatch-token hooks absent in production.
- [ ] Replace or separately authorize the private-gist headless-agent proof;
      installation tokens cannot read submitter-owned private gists.

## Data and product rollout

- [x] Produce a fresh results-v2 dry-run artifact.
- [ ] Review and approve its exact post-merge checksums (D7).
- [ ] Execute the authorized migration and verify the writer lock is released.
- [x] Define and audit the approved 118-member v1 set.
- [x] Freeze the v1 set when its reviewed PR merges (`lean-eval#540`,
      merge commit `547b00ed345bc0737dd94847d6b67cb681b6178a`).
- [ ] Complete staging OAuth/agent intake and exact-ref dispatch; wire its
      artifacts through the implemented UUIDv7 archive writer and into State.
- [ ] Demonstrate public replay and isolated private replay.
- [ ] Verify single-submission decrypt, VM destruction, and release
      reconstruction before enabling those features.
- [ ] Enable production intake and begin the four-week issue-intake adoption
      window.
- [ ] Complete leaderboard preview review and cut over with rollback retained.
- [ ] Close issue intake only after the time, incident, submitter, adoption, and
      announcement gates in the implementation program pass.

## External coordination

- [x] Coordinate the published generator contract through lean-eval#533 and
      formal-conjectures#4951.
- [x] Reproduce the whole-FC100 answer-slot/target audit: 89/100 source
      extractions pass and 87/89 of those build at LeanEval pins. Keep import
      blocked on the 11 extraction failures, two target incompatibilities,
      quoted-module decoder fix, unique-ID fix, and corrected 92-open set.
- [x] Reproduce the FC100 dependency audit before import: 100/100 declarations
      resolved, 280 closure edges, no target-to-target dependency, and one
      fail-closed `Erdos324.erdos_324.match_1` orphan recorded upstream.
- [ ] Launch FC100/open-conjectures only after the FC-owned importer supplies a
      compatible output contract.
