# Historical migration/replay execution packet

This is the compact preparation record for the one-time retained-baseline
historical migration and replay. It is not an activation record. Installing
the legacy identity and writing migrated envelopes stay blocked until the
pre-mutation packet is complete. State append and replay stay blocked until
the post-migration readback is also complete and the combined candidate
validates against the then-current production State head.

The packet reuses the existing public batch finalizer, private replay-plan
builder, archive migration validator, and State validators. It does not define
a second transaction or replay system.

## Frozen source-free inputs

| Input | Exact binding | Count |
| --- | --- | ---: |
| Retained baseline inventory | `evidence/historical-replay/inventories/bb405fbabe084e106ad5500b455a05ba1e1d54175d1964db3aebcc3b6ea3fce3.json` | 1,301 Results: 633 public, 668 private |
| Public replay plan | `evidence/public-replay/plans/d6e81393c37138f7928435e1e68235165dba6d9aab01698edae66acd6f08120e.json` | Frozen source evidence for 128 requests / 194 Results |
| Terminal public exclusions | validated State `0c943edde8a247b8670e10339b80fc65be6c0f33`; set SHA-256 `4030cda13036869e451c57a6af921f811ec9495d551f5dd8ef5fcfa809a0c882` | 8 requests / 20 Results now reviewed unavailable |
| Retained public batch selection | SHA-256 `a8451701c516c6d521d3c002aef48988a205e3e774700ec58a728332cbfe6b2a` | 120 requests / 174 Results |
| Public profile matrix | `configuration/historical-public-replay-profile-matrix-v1.json`, SHA-256 `a674707eea7a9556576c8dcbe57bcf6b4f44362d2bdfd47895fb7c783554f39c` | 35 profiles |
| Public qualification set | submissions commit `81e94fe2f4fc819300fd7d4e036f00124166784f`, profile-set SHA-256 `d44e73c7ae58adf806a3b5147e9aa1dbfe700a53fa9482f16c2aea3127e04e2e` | 35 profiles |
| Selected public profile descriptors | SHA-256 `03c1ad7bf4f5ac2c353db91df4647116f334bc812ce04238e9f84eabcabde8cd` | 34 used profiles; the complete 35-profile set remains frozen |
| Public materialized task content | SHA-256 `e8bffbc3afd93be21d51f58754e3435788fc0aae2d8109346950256d0f9cba81` | 174 tasks, 522 events when scheduled |
| Private archive crosswalk | `evidence/historical-replay/private-crosswalks/dfdcbc0da3a3526f8a26e6a69cefa41cbcd92de7608752193b742fcd92b00a67.json` | 639 bound, 29 not found |
| Private matrix source plan | `evidence/historical-replay/private-plans/d9561ad62098e0542656678f207b3360b0b295be975c292cbf729dc48d03bd5e.json` | 668 entries, 21 reused public profiles |
| Private image matrix | `configuration/historical-private-replay-image-matrix-v1.json`, SHA-256 `54ad4c237d08e5d0e298dfc8f752b25c89ce30e79b396a2256b4216a1c0f772c` | 63 images, 639 Results |
| Private Results snapshot | submissions commit `7fb2e762e5470ae1929dbe069dbcd0c8488b51d7`, store SHA-256 `9e998ab47ae719484e2ea283271086d2c66c95051837231014fd74392f4fb1c0` | 1,304 Results: 668 private |
| Private audit source | audit commit `ad356e7bc5a2d650d9902ac3f6d352a0164360bc`, inventory digest `6b8867f41a13c3ba323746988058886e5dc73da7b509deaf01ccf9c36fe8d5d4` | 1,045 archives |
| Selected schema-1 migration set | inventory digest `a8913f1c8b5073e5b7ab309ba10481b615ca4fc00e629e41a9e57962f3afebd4` | 439 unique archives |
| Pre-mutation State comparison, not append authority | production commit `9cf3b4999bae2b6faaa32ff1bf5f040c5e6f787f`; event-set digest `d3392218297ea11f6093b59e5252f3ae394887368e02cea40a58e6fd82a901b5`; public-v6 projection SHA-256 `381275d999fdd99540c6837adcda549f7cc6c8294e6431d442ce30b245057cb2` | 489 events; 0 replay series; 488 reviewed-unavailable Results |

The fixed reviewed implementation bindings are:

| Component | Exact binding |
| --- | --- |
| Migration workflow | `.github/workflows/migrate-archive-envelopes.yml`, SHA-256 `b3a61b774c986147e1954f3c5d9290bb64aa2beceadf206b3ce0317873709801` |
| Private replay controller | `.github/workflows/historical-private-replay.yml`, SHA-256 `402e8cab4e8d308cbca36ddcfe7060482a243b9c6aad29ba91b78a3dd1e672c8` |
| Public replay controller | `.github/workflows/historical-authoritative-replay.yml`, SHA-256 `a1afb9da60ddcd8bc7192d673b4cfc2e51831acc307164c252dee1a0b8126d21` |
| Migration validator | `scripts/migrate_archive_envelopes.py`, SHA-256 `988fa540773860a391e40709df12774bde179e69b9e5c77ebc743978c59992c6` |
| Private plan builder | `scripts/prepare_historical_private_replay.py`, SHA-256 `2f1ae6a6e8710a0d0983aa7c2b3f64e77ebf2322da8154c04e39be084f4355e4` |
| Public finalizer | `scripts/prepare_historical_public_authority.py`, SHA-256 `59e611fd468e700b766343adc2f3a861ed2fa3c182761ef2ba7f6efd66434d6b` |
| Migration infrastructure | Applied production template `infrastructure/aws-key-adapter/template.yaml`, SHA-256 `aac24318c973523a65b76af34b8e1408a5680f61b52c4fb996f93967253ef94d`. The completed operator procedure is not an execution input and must not be rerun. |
| Migration boundary | role `arn:aws:iam::161072922960:role/lean-eval-archive-migration-wrap-production`; environment `archive-migration-production`; review branch `archive-file-key-rewrap-v1`; confirmation `stage-envelope-migration` |
| Audit promotion contract | caller `.github/workflows/promote-archive-migration.yml`, SHA-256 `a1cde0d8663adf6fddc5bd8942f92602c391ca918f04dd84e59325460cb26c5a`; reusable audit contract commit `7a53c75c6d7c263c684ebcd54590c657c9298642`, workflow SHA-256 `b760ee6e6f04bcd061e3f15bea67c3dad33812e7cf5627b5850635d8228c8d3e` |
| Deterministic migration report | SHA-256 `faa26e1aa47eb629966db03695eda4f949b6c9804166f0047f53e09d9cc83339` |

Both replay controllers are restricted to protected `main`, use one shared
non-cancelling concurrency group, allow at most four execution attempts, and
bound each job to 360 minutes. The shared group prevents public and private
historical execution from overlapping. Cleanup has a seven-hour deadline and
each OIDC token lifetime is at most ten minutes. Public replay emits no
artifact; the private controller retains its source-free terminal receipt for
30 days.

The old plan remains immutable source evidence; it is not current enqueue
authority. The pinned State validator proves that exactly 20 of its Results now
have terminal `historical_result.replay_unavailable` roots and excludes those
subjects before event construction. The finalizer fails unless this produces
the exact 174-Result retained selection and the digests above. It never masks a
State validation conflict or chooses between competing lanes.

The public task-content digest excludes only the six State-assigned event
identity fields: `authority_event_id`, `authorized_at`,
`qualification_event_id`, `qualified_at`, `event_id`, and `occurred_at`. It
retains every authority, qualification, enqueue, profile, checker, benchmark,
and replay-task field. The ordinary finalizer continues to emit the full event
and task-set digests after the final State time window and deterministic seed
are selected.

This packet authorizes only the retained baseline hashes above. The final
issue-intake cutoff and append-only delta require a later, separate exact
packet; they do not prevent processing the retained baseline during overlap.
The new retained-baseline append is expected to contain 2,439 events and
materialize 813 replay tasks: 522 events/174 tasks for public replay and 1,917
events/639 tasks for migrated private replay. The existing 459 public and 29
private unavailable dispositions are not appended again.

## Pre-mutation authorization bindings

Fill these values only from committed canonical outputs. Do not substitute a
workflow artifact, worktree file, mutable tag, or branch head.

- [x] The 63 canonical private execution profiles are bound at submissions
      commit `c3c2a3b1617f4f90b8b2cae86738abad7dca3f0c` and protected through
      authority merge `5e7c181edef7569dcf2ecb2c33f7819adfb75b07`. Their exact
      `{path, sha256}` locators are closed by the private replay plan.
- [x] The content-addressed private replay plan has SHA-256
      `08992e62486c2b000bf4914c80cbfe734a3aa9d0d07dab481b40cd8684fe268d`
      at
      `evidence/private-replay/plans/08992e62486c2b000bf4914c80cbfe734a3aa9d0d07dab481b40cd8684fe268d.json`,
      protected through authority merge
      `5e7c181edef7569dcf2ecb2c33f7819adfb75b07`. It contains 63 profiles,
      639 `profile_qualified` entries, 29 `archive_not_found` entries, and
      zero `profile_pending` entries.
- [ ] Select the execution commit immediately before each dry-run or apply
      dispatch. Pass its full SHA as `expected_workflow_commit`; it must equal
      that run's protected submissions `main`. Require migration-workflow
      SHA-256
      `b3a61b774c986147e1954f3c5d9290bb64aa2beceadf206b3ce0317873709801`,
      private-controller SHA-256
      `402e8cab4e8d308cbca36ddcfe7060482a243b9c6aad29ba91b78a3dd1e672c8`,
      and public-controller SHA-256
      `a1afb9da60ddcd8bc7192d673b4cfc2e51831acc307164c252dee1a0b8126d21`.
      If protected `main` advances after the dry run, bind the new SHA and
      repeat the dry run before apply.
- [x] The production template is bound at SHA-256
      `aac24318c973523a65b76af34b8e1408a5680f61b52c4fb996f93967253ef94d`,
      and `archive-migration-production` directly defines only
      `AWS_WRAP_ROLE_ARN` with the dedicated migration role plus
      `AUDIT_MIGRATION_READ_KEY`. Authenticated readback bound the production
      stack at `UPDATE_COMPLETE`, last updated
      `2026-08-31T06:07:26.081Z`, with the exact migration-role OIDC trust and
      Encrypt-only v2 policy, replay v1+v2 Decrypt support, and unchanged
      ordinary v1 roles and outputs. The staging stack remained
      `UPDATE_COMPLETE`, last updated `2026-08-27T06:33:54.697Z`. No further
      infrastructure apply is part of this packet.
- [x] The exact migration inputs are audit commit
      `ad356e7bc5a2d650d9902ac3f6d352a0164360bc`, selected inventory digest
      `a8913f1c8b5073e5b7ab309ba10481b615ca4fc00e629e41a9e57962f3afebd4`,
      count 439, role
      `arn:aws:iam::161072922960:role/lean-eval-archive-migration-wrap-production`,
      environment `archive-migration-production`, isolated review branch
      `archive-file-key-rewrap-v1`, and apply confirmation
      `stage-envelope-migration`. The review branch is absent before apply.
- [ ] Legacy identity preflight: custodian Kim Morrison (`@kim-em`) must derive
      the public key from the supplied legacy identity and require fingerprint
      `SHA256:4unwBywJxfq9LsOjygB+/NRHaXdBhvxKP+a3EEpqjoE` before installation.
      Never record the private material or its path. The protected environment
      does not directly define `LEGACY_ARCHIVE_IDENTITY` before this gate.
- [x] Controller source commit
      `139b2e2db63d942222260595f3347945aee13583` binds private-controller
      SHA-256 `402e8cab4e8d308cbca36ddcfe7060482a243b9c6aad29ba91b78a3dd1e672c8`
      and public-controller SHA-256
      `a1afb9da60ddcd8bc7192d673b4cfc2e51831acc307164c252dee1a0b8126d21`.
      The profile locators bind every immutable image manifest. Both lanes use
      subject `repo:leanprover/lean-eval-submissions:environment:replay-production`,
      exact workflow references on protected `main`, and ten-minute OIDC
      tokens. Public audience
      `lean-eval-historical-public-replay-production` is limited to POST on
      `/api/v1/historical-public-replay`,
      `/api/v1/historical-public-replay/status`,
      `/api/v1/historical-public-replay/cleanup`, and
      `/api/v1/historical-public-replay/cleanup-reservation`. Private audience
      `lean-eval-historical-private-replay` is limited to POST on
      `/api/v1/replay`, `/api/v1/replay/status`,
      `/api/v1/historical-private-replay/reserve`, and
      `/api/v1/historical-private-replay/cleanup`. Both lanes use
      non-cancelling serialization, a seven-hour recovery lease, 360-minute
      jobs, four attempts, and explicit terminal dispositions.
- [x] Public projection v6 exposes only historical identity, visibility,
      profile/measurement digests, transition time, and terminal replay or
      unavailability fields. It redacts private archive, crosswalk, authority,
      and key-envelope locators. Private State intentionally retains exact
      content-addressed encrypted locators and digests; migration/replay does
      not modify Results, and no source, key material, or plaintext enters a
      public projection, artifact, log, release, or publication.
- [x] The source-bound recovery contract leaves audit `main` unchanged on
      migration failure and removes job-local identity, AWS session, and
      scratch. The isolated branch is written only by the terminal staging
      step and is deleted by promotion after its compare-and-swap readback. At
      audit head
      `7a53c75c6d7c263c684ebcd54590c657c9298642`, the pinned audit source is an
      ancestor, its 1,756 migration-touched paths have zero overlap with the
      160 intervening changed paths, and `archive-file-key-rewrap-v1` is
      absent. Replay variables are absent; production replay and historical
      public replay flags are false; Cloudflare has no `hpr-` Worker or
      `le-hpr-` application; and production State
      `fb70dd6ba14cae94b30d570818e4801884e81e04` has no active replay series.
      Recheck these live absences before identity installation; apply repeats
      the audit-head, path-overlap, and branch checks.
      Retain the migration role, protected environment, and workflow for the
      separately bound final-cutoff delta unless the lane is explicitly
      abandoned and retired.
- [x] Explicit exclusions: no legacy-key destruction, final-cutoff delta,
      intake or publication change, experimental checker, FC/disproof work,
      external PR/comment, or item outside the hashes in this packet.

For each private profile the plan builder derives and validates the fields that
must enter State: `benchmark_commit`, `toolchain`,
`lean_toolchain_blob_sha256`, `measurement_config_digest`,
`execution_profile_digest`, checker `nanoda`, and the existing profile locator
`{commit, path, sha256}`. The profile blob itself binds the immutable registry
manifest, image-source commit and source-blob closure, workflow commit and
digest, workflow run and attempt, and a passing local official-entrypoint
schema-2 file-key probe under Docker `--network none`. Its target Cloudflare
runtime tuple is accepted only when the packet-bound frozen public profile set
has one common tuple and an exact matching toolchain/component lock. The first
real migrated replay remains the serialized Cloudflare execution canary. The
packet binds the profile descriptor set instead of copying those fields into a
second format.

Completion of this section is the gate for the custodian to install
`LEGACY_ARCHIVE_IDENTITY` directly into the protected environment and for the
migration workflow to create the isolated review branch. The identity value is
never part of the packet.

## Post-migration readback and State bindings

These fields are unknowable until the packet-bound migration has produced and
validated the randomized v2 envelope bytes. They are a readback gate, not a
reason to install the identity before the pre-mutation packet is complete.

- [ ] The deterministic migration report digest plus the staged audit commit,
      tree, and exact binary patch digest relative to the pinned audit source.
      Require 439 schema-version-3 sidecars, zero changed ciphertexts and stable
      IDs, and no retained plaintext, legacy identity, AWS session, or scratch
      output.
- [ ] Immediately before promotion, bind the then-current audit `main`. Require
      the pinned source to be its ancestor and require zero overlap between all
      intervening changes and the migration-touched source and target paths.
      Apply exactly the staged patch to that current head, bind the resulting
      commit and tree, and promote only that rebased tree. After merge, require
      audit `main` to have exactly the bound tree.
- [ ] The exact production State head used for combined validation.
- [ ] One `first_occurred_at` after that State head and deterministic event
      identities derived by the canonical public and private finalizers from
      the final immutable inputs.
- [ ] The create-only public and private State candidate tree digests, event-set
      digests, materialized queue digests, event counts, and task counts after
      validating the combined graph against that exact State head.
- [ ] A read-only redacted projection of the combined candidate and proof that
      every queued baseline Result appears exactly once while all 459 public
      and 29 private reviewed-unavailable Results retain their terminal
      dispositions.

## Packet-bound execution order

1. Validate the protected private replay plan and all 63 profile locators at
   authority merge `5e7c181edef7569dcf2ecb2c33f7819adfb75b07`, then complete the
   remaining pre-mutation authorization bindings. Do not rebuild or requalify
   the completed private profiles.
2. Apply and verify the bounded migration infrastructure, and only then have
   the custodian install the legacy identity for the exact protected workflow
   run.
3. Run the archive migration workflow against the exact audit commit and
   selected inventory digest. Complete the post-migration readback, then apply
   the exact staged patch to the separately bound current audit head and
   promote only the resulting reviewed tree.
4. Run `prepare_historical_public_authority.py finalize-batch` against its
   pinned, complete State contract checkout
   `0c943edde8a247b8670e10339b80fc65be6c0f33`; the finalizer derives the exact
   20 terminal exclusions from that validated ledger. The caller supplies the
   timestamp and event-ID seed, and the finalizer requires every derived event
   to follow the ledger's latest event. Run
   `prepare_historical_private_replay.py state-events --selection full
   --append-ready` against that exact current head with non-overlapping times.
   Validate both candidate sets together against the current head before
   committing either candidate.
5. Append the reviewed candidates, enable only the required historical
   controllers, drain both queues with bounded retries, and record a terminal
   replay or reviewed-unavailable disposition for every retained-baseline
   Result.
6. After the retained-baseline queues reach their reviewed terminal states,
   remove only the installed legacy identity, AWS session, and migration
   scratch, and disable the historical replay controllers. Retain the dedicated
   migration Encrypt role and stack output, protected migration environment,
   one-shot migration workflow, and custodian-held offline master for the
   separately bound final-cutoff delta.
7. After the announced issue-intake cutoff, generate the append-only delta and
   prepare a new exact packet covering only those added Results. Never extend
   this retained-baseline packet by implication. Use that packet to migrate any
   selected delta archives, apply its exact staged patch to the separately
   bound current audit head, promote the resulting tree, and complete its
   post-migration readback.
8. Only after final-delta promotion and readback, remove the installed identity
   and session material, one-shot migration workflow, protected migration
   environment, and dedicated migration Encrypt role and stack output. The
   custodian must then destroy the
   offline master and verify that no installed or working copy remains. Retain
   v2 replay Decrypt support, the schema-3 file-key replay implementation, and
   the versioned replay/checker records. Enable the historical controllers only
   for the separately reviewed final-delta queues and disable them again after
   every Result has a terminal disposition.

At every step, an input mismatch leaves the corresponding capability disabled.
Creating and filling this packet does not itself write State, migrate an
archive, or enable replay; only the separately invoked packet-bound execution
steps do so.
