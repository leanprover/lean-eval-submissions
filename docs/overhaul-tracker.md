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
      for this bootstrap. This decision authorizes only the intake-disabled
      bootstrap; production intake still requires either the locked
      organization-owned hostname/OAuth design or an explicit recorded
      amendment to that launch requirement.
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

## Lifecycle product contracts

These are required by the authoritative product plan and remain separate from
the already implemented owner status, metadata, and publication routes.

- [x] Add the production State representation for legacy result claims and
      metadata backfill without rewriting historical facts
      (`lean-eval-state#8`, merge `6ba71d2a`). The v1 public projection remains
      byte-for-byte stable and the redacted lifecycle metadata is opt-in v2.
- [x] Add authenticated owner retraction- and repair-request writers behind a
      dark independent feature flag, including authority-derived owner
      verification, exact idempotence, causal CAS, targeted amendment and
      release-status reads, release barriers, and redacted responses. The local
      integration is bound to production State contract `501d237` and staging
      migration `6a386bb`; see
      `docs/result-amendment-owner-api.md` for the exact closed blob proof.
- [x] Version the targeted release status with monotone revision/supersedes
      proof, rebind each State repository to its exact reviewed contract, and
      document the explicit 369-request worst-case external-subrequest ceiling.
      Production State `501d237` and staging migration `6a386bb` are green; the
      runtime validates the named marker, its predecessor for revisions after
      one, and all bounded amendment history without a repository scan. The
      deployed plan allowance remains a mandatory pre-enable check.
- [x] Add model-alias, rename, consolidation, and new-canonical-identity
      request events, authorization, deterministic materialization, collision,
      hostile-input, causal, and idempotence tests, and lifecycle-aware public
      projection behavior. The private State contracts are merged in
      production (`lean-eval-state#12`, merge `889e07e3`) and staging
      (`lean-eval-state-staging#15`, merge `494a6746`, portable identity commit
      `61385eee`). The consumer is merged and live as
      `lean-eval-leaderboard#75` (merge `89be802f`); production Pages run
      `32747172862` published the redacted schema-v4 projection successfully.
- [x] Add the bounded authenticated model-identity producer for requests,
      decisions, aliases, renames, and complete-graph consolidation with exact
      signed-session owner derivation from the OAuth and verified-agent issuers,
      numeric-ID/login maintainer binding,
      targeted State CAS, closed payloads, permanent alias collision handling,
      independent dark gates, rollback/health guards, and a conservative
      400-subrequest ceiling. Both environments keep both gates false and the
      maintainer list empty. Live OAuth and agent session issuance, paid-plan
      allowance, dark staging E2E, and an enablement decision remain rollout gates; see
      `docs/model-identity-owner-api.md`.
- [x] Add the protected, hard-32-view reverse-impact index and complete-graph
      atomic State producer for model consolidation. Production State contract
      `6799522f` and staging mirror `9fc7c431` supply the exact index; the Worker
      reproves their ancestry and source root entries, reads and validates every
      source and target member, rematerializes every transitive predecessor and alias,
      removes the old component, and creates the sorted target union in one
      bounded CAS. Health reports `atomic_reverse_impact_v1`. This is source-only:
      all owner/maintainer gates remain false pending paid-plan and dark staging
      qualification.
- [x] Merge the inert model-identity staging qualification and automatic/manual
      recovery contract (`lean-eval-submissions#1371`, exact reviewed head
      `caaae54c`, merge `029b69ce`). Every job retains an impossible source-ref
      gate; the workflows bind stable/original/triggering actor identity,
      first attempts, operation-specific credentials, journal/lease recovery,
      exact State commit/tree transitions, and final disabled health. PR CI
      `32815289877` and Worker check `32815289745` passed without reaching an
      environment. Protected rollout `32815495847` then promoted and deployed
      the exact merge to staging and production, passed its staging canary, and
      retained every impossible source gate; follow-up canary `32815898527`
      passed. Live health binds both intake Workers to `029b69ce` with intake
      and every result/model owner or maintainer API disabled.
- [x] Repin the disabled Worker coherently to reviewed production State
      `b0a30e3a` (`lean-eval-submissions#1383`, merge `0d52fb66`) and complete
      protected rollout `32831767076`. Exact staging deployment and smoke,
      promotion canary, provisional disabled production deployment, disabled
      replay/container rollout, broker verification, and final protected-State
      verification all passed. Every intake lease and enablement step was
      skipped. Credential-free readiness run `32832326810` then passed and
      closed monitor incident `#1310`.
- [ ] Implement the persistent model-identity qualification harness, require
      protected-environment reviewers with self-review prevention, provision
      the five separately scoped ephemeral credentials, prove quiescence and
      rollback, and run the staging qualification. The merged source scaffold
      is deliberately unusable until all of those prerequisites exist; it does
      not authorize a State write or either public model-identity gate.
- [x] Add State problem-repair request and maintainer decision events with
      explicit revision/causation rules without rewriting accepted records
      (`lean-eval-state#13`, merge `0c875994`) and the targeted private
      release-status contract in draft State PR `#16`.
- [x] Add authenticated maintainer repair-decision routes and State writers
      using the qualified human identity boundary, including exact results-Git
      comparator tuple/digest recomputation. The credentialed comparator binds
      the protected Results blob and exact benchmark manifests; the State
      writer independently recomputes both challenge IDs and the envelope
      digest before an append.
- [x] Qualify the separate maintainer identity configuration and feature gate
      while false in both environments, with rollback validation and health
      evidence that never disclose the configured identity list. The routes
      remain unavailable while the tracked gate is false and the list empty.
- [x] Add State owner-retraction request, maintainer decision/override, and
      terminal retraction events, including release and leaderboard
      consequences (`lean-eval-state#13`, merge `0c875994`), plus the dark owner
      request API described above.
- [x] Add authenticated maintainer retraction-decision, override, and terminal
      routes and State writers using the qualified human identity boundary.
- [x] Add hostile-input, owner/maintainer authorization, idempotence, causal
      conflict, and public-redaction tests for every amendment flow.
- [x] Complete the final staging apply/reject canary for the targeted `eri1_…`
      permanent effective-identity reservation contract. The online aggregate
      read is removed; production's empty set and staging's two reservations
      are migrated and validated, and the runtime uses atomic create/confirm
      semantics. Retry `32792905120` durably applied the repair from
      `2436d631` to `9844e4b5` before failing closed on a transient broker
      response. Exact successful run `32793103590` started at `9844e4b5`,
      idempotently reverified the apply, and completed request/reject at final
      State `cc52d7c2`: the apply candidate is permanently reserved, the reject
      candidate remains absent, and all four fixed events are present. Intake
      and both public amendment gates stayed false; the temporary credential
      was revoked and the one-shot surface is retired. See the rollout
      runbook's collision-index gate.

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
- [x] Run the one authorized receipt-backed authoritative staging replay. Run
      `32694194843` reached an explicit retryable `replay.failed` terminal at
      staging State commit `c3c677cc` with reason `runner_lost`; no successful
      replay evidence was fabricated. Replay was immediately disabled again in
      both environments by `lean-eval-submissions#1299` at `ffd9a473`, deploy
      run `32700136536` passed, both live health endpoints report disabled, and
      Cloudflare reports zero active or assigned replay containers.
- [x] Complete and durably retain the first full historical public GitHub
      evidence pass after workflow-contract review. Sixteen successful runs
      `32718053904`–`32719340876` at exact source `5746f90e` covered all 315
      requests / 633 public results. The reviewed aggregate resolves 69
      requests / 135 results and leaves 246 / 498 explicitly pending; it has
      SHA-256 `13a0d95b…` and is checked in with complete shard provenance in
      [`historical-public-evidence-rerun.md`](historical-public-evidence-rerun.md).
- [x] Rerun the public Gist lane without the repository-scoped installation
      token. Sixteen sequential successful runs `32768996061`–`32770548866` at
      exact source `6c13c245` resolved all 57 formerly permission-bound Gist
      requests. The new aggregate resolves 126 requests / 192 results and
      leaves 189 / 441 pending, has SHA-256 `8122b4ee…`, and is checked in with
      complete shard provenance in
      [`historical-public-gist-probe-rerun.md`](historical-public-gist-probe-rerun.md).
- [x] Rerun the complete public-evidence corpus with the closed legacy
      adjudication registry. Sixteen successful runs `32794597497`–`32795629591`
      at exact source `ba5f5784` resolve 128 requests / 194 results and leave
      187 / 439 explicitly `source_unavailable`, with every indeterminate,
      ambiguous, unreviewed, and missing-evidence bucket at zero. The immutable
      aggregate has SHA-256 `ba816b52…`; complete provenance is in
      [`historical-public-adjudicated-evidence-rerun.md`](historical-public-adjudicated-evidence-rerun.md).
- [ ] Inventory every historical accepted result at the migration cutoff and
      deterministically classify it as public-source replayable,
      private-archive replayable, or explicitly unavailable with a reviewed
      reason. The public evidence pass removes all unreviewed workflow-contract
      classifications, but 439 public results still require source recovery or
      an explicit unavailability policy, and all 668 private results still
      require the separately credentialed archive-migration lane.
      The contract-only cutoff inventory is now complete and durably reviewed:
      protected run `32790927560`, attempt 1, at immutable source `ae1a9714`
      reproduced canonical store digest `14e8c868…` and committed exact inventory
      `bb405fba…`, containing all 1,301 accepted results (633 public-probe
      pending and 668 private-migration pending). This closes only the inventory
      handoff; none of those pending classifications is replay qualification or
      a permanent-unavailability decision.
      Because issue intake is still open, the reviewed inventory is now an
      explicit baseline. The append-only delta contract rejects deletion or
      mutation of any baseline entry and source-minimally captures all later
      accepted results for the final announced cutoff. At source `7eb77aa8…`
      the live delta is three public results; this is diagnostic, not the final
      freeze, and must be rerun and durably reviewed when issue intake closes.
      The deterministic private crosswalk contract now proves, on the pinned
      read-only snapshots, 639 exact archive bindings and 29 explicit
      `archive_not_found` orphans with no ambiguity or metadata conflicts; see
      `docs/historical-private-archive-crosswalk.md`. This diagnostic did not
      migrate/decrypt archives or authorize replay, and the 29 orphans are not
      permanent-unavailability evidence.
- [ ] Enqueue and execute the complete historical replay corpus under exact
      original pins, recording terminal verdicts/statistics or explicit
      unavailability; the isolated staging proofs above do not satisfy this
      corpus gate.
      Resolved public evidence now has a deterministic source-free seed-plan
      contract that binds the exact source, benchmark/toolchain, evaluator,
      workflow, issue, Results snapshot, and aggregate identities. Protected
      run `32722572097` reproduced it twice from immutable tag `d0807084…`;
      the exact plan (`2b00c965…`) and toolchain registry (`5144fc19…`) are
      permanently checked in under their digest-derived evidence paths. That
      conservative plan covers the first pass's 69 resolved requests. Protected
      run `32799633419` at exact immutable source `2e7239e7…` subsequently
      reproduced the final adjudicated 128-request / 194-result plan twice. Its
      exact plan (`d6e81393…`) and 35-commit toolchain registry (`4f2f3737…`)
      are retained under digest-derived evidence paths. All resolved public
      request groups are now deterministically planned. The final source-free
      35-image build-readiness matrix (`a674707e…`) binds that exact plan,
      registry, and component lock; all entries remain unqualified, and none
      is enqueued or executed. Registry publication and staging qualification
      are separate phases: distinct create-only benchmark image builds may run
      in parallel, while every deployment plus its two probes stays serialized
      through the single isolated replay-disabled qualification Worker.
      The controlled qualification batch has completed at least 13 of 29
      entries. Exact isolated qualification run `32832967763` passed deployment
      and both probes for the next selected entry; its separate authority
      profile is not counted here. This records progress only, not completion
      of the matrix or authorization to execute the corpus.
      Activation
      remains fail-closed on `legacy_public_result_replay_authority_v1` because
      current State replay materialization admits only modern
      `result.recorded` submission lifecycles, not historical
      `result.claimed` records; no synthetic submission/archive authority was
      created. Production State has no historical claim anchors, so the
      required follow-up is a system-owned
      `historical_result.replay_authorized` event over one exact seed
      result/evidence tuple, followed by the ordinary replay lifecycle without
      changing acceptance, publication, credit, or owner metadata.
      Authorization remains separate from the unresolved historical-toolchain
      execution-profile gate; it does not claim the current v4.33 profile can
      execute older or prerelease toolchains.
      The distinct historical queue now has a source-free controller and a
      manual serialized production workflow. It binds
      the exact State queue, authority plan, committed qualification profile,
      profile matrix, runner contract, and source handoff. It uses State's exact
      canonical bytes and reducer order, refuses malformed re-enqueue or unknown
      transitions, refuses concurrent work, and prepares only a stale
      `runner_lost` recovery after the finite lease. The dedicated executor,
      exact attempt envelope, confirmed-destruction verdict, production State
      CAS lane, and exact repository/Gist source adapters are implemented. The
      only typed planning blocker is the three-attempt limit. The workflow
      remains dark on the absent
      `HISTORICAL_PUBLIC_REPLAY_CONTROLLER_ENABLED` variable; its protected
      production credentials are separate launch actions. No historical
      started event, executor invocation, or terminal append has occurred. See
      [`historical-authoritative-replay-controller.md`](historical-authoritative-replay-controller.md).
- [ ] Expand independent-kernel validation from the one-result shadow smoke to
      a checker-series/corpus report with separately recorded
      accept/reject/decline/crash/timeout outcomes and an explicit promotion
      decision.
      The source-free version-1 preparation/report contract now specifies exact
      series, inventory, attempt, and shard bindings; unavailable and pending
      states; deterministic full-coverage aggregation; hostile validation; and
      human-only promotion. Its offline runner-record adapter now verifies
      operator possession of exact raw replay/export inputs, validates attested
      source-free records, and emits
      schema-valid observation receipts without executing a process or gaining
      a credential interface. The `source_free` value remains the reviewed
      runner's attested assertion, not a fact independently established by the
      adapter. This item stays open until the real historical input artifacts
      exist, an approved exact-image runner produces those records, and the series runs
      over the complete reviewed historical corpus and disagreements are
      adjudicated; the fixture and one-result smoke are non-authoritative. The
      proposed closed runner wire contract now specifies raw lean4export 3.1.0
      NDJSON, canonical Mathgraph config bytes/argv, and structured transcript
      and attestation objects for a future runner. The new offline integration
      validates that complete chain against exact series, inventory, and shard
      identities and emits only existing runner records, closing the formerly
      unbound attempt/adapter gap. The authoritative and historical-public
      replay image sources also prepare an exclusive exact-byte solution-export
      capture at comparator's live checker boundary. The authoritative PR
      source-build preflight is not qualification; the historical image remains
      unbuilt, both are unqualified, and the Worker has no
      durable source-free handoff yet. The semantic validator and adapter
      deliberately block candidate exit `1`, which the pinned producer uses for
      both rejection-like errors and internal failure; a structured upstream
      producer change is still required before a `rejected` corpus outcome can
      be recorded safely. A staging-only accepted-result qualification lane now
      binds issue 906's already-reproduced public `two_plus_two` result, the
      exact evaluator capture, a source-locked Mathgraph builder, the proposed
      `--result-file` protocol, and source-free attempt attestations. It is
      deliberately hard-blocked while Mathgraph PR #51 remains unmerged and
      has not been dispatched, so it closes no execution or promotion gate.
      The current historical profile evidence exercises
      only the runtime boundary; a probe that actually runs Lean, comparator,
      and `replay-measure` to an accepted result is a hard prerequisite before
      any kernel handoff or State append.
- [ ] Enable production intake and begin the four-week issue-intake adoption
      window.
- [x] Complete leaderboard preview review and cut over with rollback retained
      (`lean-eval-leaderboard#72`; Pages run `32557778003`).
- [ ] Close issue intake only after the time, incident, submitter, adoption, and
      announcement gates in the implementation program pass.

## Production and operational readiness

- [x] Make production intake enablement, normal deploy smoke, credential
      preflight, and rollback agree on one explicit expected intake state;
      keep the tracked production value disabled until the launch review. The
      finite-lease controller merged in `#1305`; protected deployment
      `32724294780` at `685265e6` proved the protected production State and
      final disabled state while all lease/enablement steps were skipped.
      State-pin recovery `#1320` then deployed exact commit `71650c9d` in
      protected run `32728324814`, proved production contract `82a036df`
      before and after finalization, and again skipped every lease and durable
      enablement step.
- [x] Provide a commit-coherent rollback procedure for the intake Worker,
      private broker, and replay Worker/container deployment unit. The
      qualified protected workflow merged as `lean-eval-submissions#1300` at
      `f5f830d0`; main deploy run `32700644989` passed staging and production
      with intake and replay disabled. State stays append-only and
      forward-corrected.
- [x] Merge and qualify the exact staging promotion canary required by the
      program. The tracked implementation binds one deterministic, withheld,
      deliberately rejected synthetic intake to each protected-main
      commit/controller-run/attempt tuple; proves source-App connectivity, a
      real forward-only State sibling collision plus rebuilt retry, and a
      dedicated source-free no-op dispatch through the actual broker and Cron
      reconciliation while ordinary intake stays disabled; and blocks
      production deployment unless it succeeds. Fixed-shard discovery also
      reconciles pending prior workflow attempts across Worker deployments.
      The stale-binding retry repair merged in `#1314`; the presentation-time
      contract and append merged as production State `#15` and staging State
      `#14`; the future time-order repair merged in `#1318`. Protected run
      `32728324814` passed the exact canary before production deployment and
      advanced staging State to `64eb3f9f`; post-push validator `32728600770`
      passed.
- [x] Validate the release controller's State and release-repository write
      credentials without AWS decrypt or publication. The final controller
      merged as `lean-eval-releases#10` at `57ab3634`, and exact-main validation
      run `32719159678` passed. Protected preflight `32723471497` bound that
      commit to production State `0c875994`, validated its sole initialization
      event and six deterministic views, and passed both exact-ref no-op
      dry-run pushes while `PUBLICATION_ENABLED` remained absent. This proves
      the current keys reach receive-pack, not a real ref update, audit read,
      AWS unwrap, decrypt, reconstruction, or publication.
- [x] Merge the hardened credential boundary and exact reconstruction checks
      as `lean-eval-releases#18`, current main `90dadc87`. Post-merge run
      `32832191302` passed all 118 release-tooling tests, exact pinned-State
      integration, and the intentionally publication-disabled job. This did
      not update live AWS trust, assume an AWS role, unwrap an archive, mutate
      State, or publish a release.
- [ ] Pass the credentialed staging release smoke after the live AWS OIDC trust
      update. Runs `32617539355` and `32624640050` both failed at STS role
      assumption before Lambda invocation or decrypt. Keep publication disabled
      until this smoke and its separate launch gate pass. Production State
      currently has no accepted submission or due release work.
- [x] Add the publication-disabled confidentiality-incident recovery planner
      (`lean-eval-releases#8`, merge `d66c8dd`), including strict original
      publication bindings, canonical shared-path classification, and a
      fail-closed remediation plan that performs no mutation.
- [x] Complete and merge the immutable `release.removed` State correction
      contract (`lean-eval-state` commit `940a2a4f`), then bind the read-only
      recovery planner to its exact schema, materializer, projection, and
      correction skeleton (`lean-eval-releases#9`, merge `ded9463`).
- [ ] Qualify the protected repository-removal/history-cleanup procedure and
      reviewed operator append of `release.removed` without rewriting Results.
      The merged planner deliberately performs neither containment nor the
      State append.
- [x] Record and deploy the readiness monitor, alert destination, severity
      owner, support contact, emergency intake-pause owner, and response
      procedure (`lean-eval-submissions#1301`, merge `58d88268`). Main deploy
      run `32701229915` passed in both environments with intake/replay disabled;
      protected live monitor run `32701461244` then verified the exact deployed
      commit and reconciled the bot-owned incident state.
- [ ] Move the production hostname and OAuth Apps to organization ownership as
      required by the locked program, or record an explicit reviewed amendment
      authorizing the temporary `workers.dev`/owner arrangement for launch.
- [ ] Reconcile the rollout runbook, infrastructure current-version table,
      credential rotation/revocation data, and public implementation tracker
      after each remaining protected rollout.

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
      deterministic provenance as a reference implementation
      (`formal-conjectures#4951`, previously green at head
      `2654e42de2026de6cdb248ad5ed0f1c7d659c8fa`). The draft must still be
      reconciled with current FC `main`, current Lean pins, and maintainer
      review before it is launch-ready.
- [ ] Launch FC100/open-conjectures only after generator pins advance, the
      importer receives FC maintainer review, and the production launch gates
      are satisfied.
- [ ] Complete the human-authored flavour-text/hint audit; tooling may inventory
      missing or stale text, but agents do not author hints.
- [ ] After FC100 proof-only launch, coordinate comparator disproof support and
      add the generator/manifest disproof contract once the upstream comparator
      integration lands.
