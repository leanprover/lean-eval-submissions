# [overhaul] implementation tracker

Tracks implementation and production rollout of the plan in
[lean-eval#536](https://github.com/leanprover/lean-eval/pull/536) and the
[public implementation program](https://gist.github.com/kim-em/cd6ac1c049f459ef9aa37d6cf551d9e4).

The detailed decision and operator procedures live in
`docs/overhaul-rollout-runbook.md`. Use this single tracker rather than opening
one issue per lane.

## Decisions

- [ ] D1: choose public or private raw production/staging State repositories.
- [ ] D2: approve the v1 cutoff, mechanical membership rule, exceptions, and
      freeze date.
- [ ] D3: approve, revise, or reject each software-verification statement.
- [ ] D4: record the organization-controlled Cloudflare account and
      administrators for `lean-lang.org`.
- [ ] D5: approve the initial State-writer credential model and rotation owners.
- [ ] D6: approve key recovery, one-submission capability, contributor
      acknowledgement, and license wording before private intake/release.
- [ ] D7: approve a fresh results-v2 migration report's exact source commit,
      record count, and output digest.

## Repository and contract foundations

- [ ] Create and publish `lean-eval-generator`.
- [ ] Create and publish production State with the D1 visibility.
- [ ] Create and publish staging State with the D1 visibility.
- [ ] Create and publish disabled `lean-eval-releases` tooling.
- [ ] Replace LeanEval's local generator path with an exact public commit SHA.
- [ ] Verify generator byte parity after the remote pin.
- [ ] Configure State rulesets, append-only validation, backups, and restore
      drills.

## Existing-repository PRs

- [ ] Catalog lifecycle/tags/audit PR.
- [ ] Results-v2 compatibility and migration-tooling PR.
- [ ] Intake-disabled Worker, deployment, rollback, threat-model, and
      infrastructure-ledger PR.
- [ ] Leaderboard results-v2 preview PR.
- [ ] Software-verification draft PR after D3 review.
- [ ] LeanEval generator-consumer PR after the public generator pin exists.

## Cloudflare bootstrap

- [ ] Create protected `cloudflare-staging` and `cloudflare-production` GitHub
      environments with distinct scoped deployment tokens.
- [ ] Deploy staging automatically with intake disabled and record the Worker,
      route, account, zone, and version identifiers.
- [ ] Promote the same commit to production and verify the structured health
      response.
- [ ] Install distinct State and readiness secrets in each Worker.
- [ ] Complete and record a production rollback drill.

## Data and product rollout

- [ ] Review a fresh results-v2 dry-run artifact.
- [ ] Execute the authorized migration and verify the writer lock is released.
- [ ] Create and freeze the approved v1 set.
- [ ] Complete staging OAuth/agent intake, exact-ref dispatch, UUID archive
      writing, and State event linkage.
- [ ] Demonstrate public replay and isolated private replay.
- [ ] Complete key recovery, decrypt, VM destruction, and release reconstruction
      drills.
- [ ] Enable production intake and begin the four-week issue-intake adoption
      window.
- [ ] Complete leaderboard preview review and cut over with rollback retained.
- [ ] Close issue intake only after the time, incident, submitter, adoption, and
      announcement gates in the implementation program pass.

## External coordination

- [ ] Coordinate the published generator contract through lean-eval#533 and
      formal-conjectures#4951.
- [ ] Validate FC answer-slot types under LeanEval's pinned target environment.
- [ ] Reproduce the FC100 dependency audit before import.
- [ ] Launch FC100/open-conjectures only after the FC-owned importer supplies a
      compatible output contract.
