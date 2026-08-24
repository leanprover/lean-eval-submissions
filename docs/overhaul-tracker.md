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
- [x] Merge the dark owner/maintainer decision implementation
      (`lean-eval-submissions#1331`, merge `045f986a`), the source-free offline
      independent-kernel runner-record adapter (`#1332`, merge `fe081f0f`), and
      the read-only historical authority-preparation lane (`#1333`, merge
      `a7214278`). The historical qualifier recreation repair (`#1334`, merge
      `7dab1b31`), independent-kernel runner wire contracts (`#1336`, merge
      `09aad9c`), and per-Cron scheduled-dispatch budget (`#1337`, merge
      `a534c23`) followed. Historical authority checkout-proof repair `#1338`
      then merged as `99ca401c`. The immutable workflow-tag split `#1339`
      merged as `c07e002`; protected main CI `32763411338`, both concurrent
      same-SHA tag minters (`32763411352` and deploy run `32763411480`), the
      staging canary, and the complete disabled production rollout passed.
      Profile evidence `#1341`, recovery-ref guard `#1342`, complete offline-test
      discovery `#1344`, and the remaining manual wrong-ref guards `#1345`
      subsequently merged through source main `02ec652`; exact-main CI runs
      `32763727316`, `32764211535`, `32764925797`, and `32766363439` passed.
      Immutable-promotion hardening `#1343` then merged as current source main
      `c71fc80`; exact-main CI `32767617157` and standalone promoter
      `32767617218` passed. Protected dark rollout `32767617219` and exact
      canary `32767852651` then passed; exact live health binds both environments
      to `c71fc80` with all intake/replay/owner/maintainer gates false and only
      staging acceptance/canary true.
      These merges add only dark runtime,
      source-free preparation, or fail-closed contract machinery: every
      owner/maintainer gate, replay path, intake path, and publication path
      remains disabled.
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
- [ ] Complete the final staging apply/reject canary for the targeted `eri1_…`
      permanent effective-identity reservation contract. The online aggregate
      read is removed; production's empty set and staging's two reservations
      are migrated and validated, and the runtime uses atomic create/confirm
      semantics. PR `#1331` merged the complete dark implementation as
      `045f986a`; protected deployment `32755550105` passed staging, the
      promotion canary, and disabled production finalization against production
      State `501d237` and staging State `6a386bb`. Latest protected deployment
      `32762060004` passed the same disabled gates at `99ca401c`. The
      result-specific apply/reject canary remains deliberately unexecuted, so
      the owner and maintainer gates stay false and the maintainer list stays
      empty; see the rollout runbook's collision-index gate.

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
- [ ] Inventory every historical accepted result at the migration cutoff and
      deterministically classify it as public-source replayable,
      private-archive replayable, or explicitly unavailable with a reviewed
      reason. The public evidence pass removes all unreviewed workflow-contract
      classifications, but 498 public results still require retry or
      adjudication and all 668 private results still require the separately
      credentialed archive-migration lane.
- [ ] Enqueue and execute the complete historical replay corpus under exact
      original pins, recording terminal verdicts/statistics or explicit
      unavailability; the isolated staging proofs above do not satisfy this
      corpus gate.
      Resolved public evidence now has a deterministic source-free seed-plan
      contract that binds the exact source, benchmark/toolchain, evaluator,
      workflow, issue, Results snapshot, and aggregate identities. Protected
      run `32722572097` reproduced it twice from immutable tag `d0807084…`;
      the exact plan (`2b00c965…`) and toolchain registry (`5144fc19…`) are
      permanently checked in under their digest-derived evidence paths.
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
      `lean-eval-submissions#1333` merged the source-free preparation lane as
      `a7214278`: it accepts only exact successful schema-v2 qualification ZIPs,
      verifies commit-object and run-attempt provenance, freezes a digest-bound
      profile, and can locally validate proposed authority/profile/enqueue
      inputs against pinned production State `501d237`. It cannot append State,
      enqueue replay, deploy, or enable a flag. First qualification attempt
      `32756105389` failed closed at probe 2: probe 1 succeeded, but same-nonce
      post-destroy recreation returned HTTP 500 `input_transfer_failed` at the
      second input-write boundary. Candidate artifact `9531319399` remains
      unqualified and diagnostic artifact `9531347000` is failure evidence, not
      success authority. PR `#1334` merged the recreation stabilization as
      `7dab1b31`; immutable-tag run `32759973060` then passed both source-free
      destroy/recreate probes against historical image source `f358e34e`,
      benchmark `11081d34`, and OCI manifest
      `sha256:c4b1a4f7c7ad3339d7491a06000078a4669490c2f324836ef4b26cf0bafd8b30`.
      Exact candidate ZIP `9532314439` has package SHA-256
      `cc03d52dfb11ba72fc483411af862ff0da7dfc0fa1715a624e4fc7190df16d77`;
      exact staging ZIP `9532372346` has package SHA-256
      `30652b5dad0b3c43aef29845c59d65e00883aede0054cb2fb8d0120f7fceba03`.
      Their closed JSON still says `qualification_status: unqualified`: the run
      proves the runtime boundary but does not itself create State authority.
      Source-free preparation run `32760508212` verified the immutable source,
      exact successful run attempt, artifacts, and image checkout, then failed
      closed before output because the Git checkout identity/cleanliness changed.
      It uploaded no preparation artifact. PR `#1338` fixed the checkout-origin
      proof and merged as `99ca401c`; its exact-artifact local reproduction
      emitted only `activation_status: blocked` with profile digest
      `0886d3624de67d0ba1cb00657f66c5f7304743773a024509fceda6ae8f4ff660`.
      Exact-main CI `32762060075` passed. Immutable-tag preparation retry
      `32762356637` succeeded against the same exact qualification inputs and
      uploaded only blocked, source-free review artifact `9533151284` (4,753
      bytes; package SHA-256
      `53dc720b53939c1131bb7b3d7a38ae7652df20fca5c6c3dad0566d3e524302d2`).
      Its proposed profile digest is
      `0886d3624de67d0ba1cb00657f66c5f7304743773a024509fceda6ae8f4ff660`;
      its profile file SHA-256 is
      `52e94733725e53e514ded4f21a305a370f4f64d17c67b8479faa7821dd64489e`.
      Evidence-only PR `#1341` committed those exact byte-identical profile
      bytes as source main `9a256695`; exact-main CI `32763727316` passed.
      `activation_status` remains `blocked`. The isolated probes establish only
      the runtime boundary, not an actual Lean/comparator/`replay-measure`
      execution. No State append, enqueue, or authority exists. The first
      historical append also remains blocked on the separate State-contract
      hardening review; any eventual candidate must be regenerated after those
      contracts and the accepted execution qualification are complete.
      Authorization remains separate from the unresolved historical-toolchain
      execution-profile gate; it does not claim the current v4.33 profile can
      execute older or prerelease toolchains.
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
      a credential interface. This adapter merged in
      `lean-eval-submissions#1332` as `fe081f0f`. The `source_free` value remains
      the reviewed runner's attested assertion, not a fact independently
      established by the adapter. This item stays open until the real historical
      input artifacts exist, an approved exact-image runner produces those
      records, and the series runs
      over the complete reviewed historical corpus and disagreements are
      adjudicated; the fixture and one-result smoke are non-authoritative.
      `lean-eval-submissions#1336` merged the closed raw lean4export 3.1.0
      NDJSON, canonical Mathgraph config bytes/argv, and structured transcript
      and attestation contracts as `09aad9c`; exact CI `32760637011` and
      protected deployment `32760637034` passed with replay disabled. The
      current record adapter does not consume this wire format. Its semantic
      validator deliberately blocks candidate
      exit `1`, which the pinned producer uses for both rejection-like errors
      and internal failure; a structured producer change is still required
      before a `rejected` corpus outcome can be recorded safely. Runner
      integration must also supply the series and inventory identities needed
      to derive the corpus attempt ID; version 1 marks that carried label
      explicitly unbound.
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
      was rebound to State v2 by `lean-eval-releases#12`, merge
      `f7d088cf8063760b158067a585d650ef23da63db`; exact-main run `32751142924`
      passed. Publication-disabled synthetic reconstruction `32756570115`
      passed at that commit and production State `501d237`. Fresh protected
      preflight `32756572085` validated and materialized the same exact contract
      and passed both exact-ref no-op receive-pack checks while
      `PUBLICATION_ENABLED` remained absent. Live ruleset `21094005` is active
      and now lists both `DeployKey` and `kim-em` as always-allowed bypass
      actors with deletion, non-fast-forward, linear-history, pull-request, and
      exact `validate` protections unchanged; production controller deploy key
      `161040898` is verified and write-capable. This proves configuration and
      no-op credential reachability, not a real ref update, audit read, AWS
      unwrap, decrypt, reconstruction, or publication.
- [ ] Pass the credentialed staging release smoke after the live AWS OIDC trust
      update. Runs `32617539355` and `32624640050` both failed at STS role
      assumption before Lambda invocation or decrypt. Release PR `#13` hardened
      the exact staging audit checkout and merged as
      `e25b1a76db14933f294cee8049be2778fdcdd856`; exact-main validation
      `32758548453` passed both validation and publication-disabled jobs. PR
      `#14` then merged the isolated, read-only production audit-key preflight as
      `9278e771216cf55f345c0088823a97653b1ef507`; exact-main validation
      `32762170467` passed. Isolated proof `32762314683` then passed with the
      publication latch off: blobless private-audit upload-pack reads worked and
      receive-pack was denied. It made no write and used no AWS authority or
      artifact. The credentialed staging smoke remains undispatched. Run and
      review the credentialed staging proof. Keep publication disabled until it
      and the separate launch gate pass.
      Production State currently has no accepted submission or due release work.
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
      after each remaining protected rollout. This reconciliation is current
      through submissions main and deployed runtime `c71fc80`. Pending PRs
      `#1340` (public evidence) and `#1346` (kernel adapter), plus the separate
      historical State-contract branch, require a follow-up reconciliation
      after they complete and produce any protected evidence.

### Remaining launch blockers as of 2026-08-24

- Historical public replay now has exact successful runtime-boundary evidence
  `32759973060` and blocked source-free preparation artifact `9533151284` from
  successful retry `32762356637`; evidence-only profile PR `#1341` is merged as
  `9a256695`. Before any State append or ordinary enqueue, produce an accepted
  qualification that actually runs Lean, comparator, and `replay-measure`, and
  finish the separate State-contract hardening. The full public/private corpus
  remains a later gate, with public resolver PR `#1340` still pending.
- Independent-kernel promotion still needs real reviewed runner inputs, the
  approved exact-image execution, complete series aggregation, disagreement
  adjudication, and an explicit human promotion decision.
- Owner/maintainer mutation stays dark until the exact staging apply/reject
  canary passes and the deployed plan's 369-external-subrequest allowance is
  confirmed.
- Release publication still needs the live credentialed staging AWS unwrap proof
  and explicit launch review. Isolated audit-key proof `32762314683` passed
  read-only with receive-pack denied. AWS, real Git writes, State callback, and
  publication remain unexercised by the fresh publication-disabled proofs;
  `PUBLICATION_ENABLED` remains absent.
- Production intake still needs the locked organization-owned hostname/OAuth
  resolution or a reviewed amendment, the final launch review, and the finite
  lease canary. PR `#1337` merged the separate application-level 400-subrequest
  scheduled-dispatch budget as `a534c23`; exact-main CI `32761440535` and
  protected deployment `32761440511` passed, including the staging canary and
  exact disabled live health. The scheduled-Cron bound is closed. The
  369-request maintainer-route analysis remains a separate bound. Production AWS
  archive/replay role variables remain disconnected.
- FC100 still waits for draft `formal-conjectures#4951` at
  `bd282283515efaeeb7eaa0903379f8fb2a2e4357` to finish its in-progress project
  build, receive maintainer review, and merge, and for the production launch
  gates above. The whole-set audit and Comparator pilot are green; no current
  merge-conflict blocker is claimed.

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
      (`formal-conjectures#4951`, current draft head
      `bd282283515efaeeb7eaa0903379f8fb2a2e4357`). The whole-set audit and
      Comparator pilot are green, while the project build is still in progress;
      the draft remains open, blocked, and review-required until its maintainers
      review and land it.
- [ ] Launch FC100/open-conjectures only after generator pins advance, the
      importer receives FC maintainer review, and the production launch gates
      are satisfied.
- [ ] Complete the human-authored flavour-text/hint audit; tooling may inventory
      missing or stale text, but agents do not author hints.
- [ ] After FC100 proof-only launch, coordinate comparator disproof support and
      add the generator/manifest disproof contract once the upstream comparator
      integration lands.
