# Historical migration/replay execution packet

This is the compact preparation record for the one-time retained-baseline
historical migration and replay. Archive migration and audit promotion are
complete. It is not a replay activation record: State promotion remains
blocked until the staged candidate's canonical binding is committed and
revalidated against the then-current production State and audit heads.

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
| Migration workflow | `.github/workflows/migrate-archive-envelopes.yml`, SHA-256 `f1d00a45092a39afa0a0bc6be4cc9a319be85876e29a9ac20334f41f0aa0eb99` |
| Private replay controller | `.github/workflows/historical-private-replay.yml`, SHA-256 `c58077160ddd969b482867057c1b6404e2370bb99f4b6b3aeb665744bf9e70f6` |
| Public replay controller | `.github/workflows/historical-authoritative-replay.yml`, SHA-256 `7d3d1b5c1a231d73db186331dbc6c52ca992e470a1fefbb5588aa0882fe14a74` |
| Two-lane driver | `.github/workflows/historical-replay-two-lane-driver.yml`, SHA-256 `5ce9e738e66b9ba3f1dcc30eef2264b9aa720d01a772ce2a9370c7888a65ad21` |
| State review and promotion workflow | `.github/workflows/append-historical-baseline-state.yml`, SHA-256 `05ff5d0cdc39a4bc275a42fbe49b8771547db29a7c86f150e610d03c48656a17` |
| State candidate closer | `scripts/prepare_historical_baseline_state_batch.py`, SHA-256 `8d8112571223316a5fd9f499b7d18ea63c530ce8f2333ffc0150211a77f1d415` |
| State review verifier | `scripts/review_historical_baseline_state_batch.py`, SHA-256 `4a7311a8f9e07a83b44d70c26f97c7db4a4c8eae7f3c45c8d55d8cba375f4d4b` |
| State batch expectation | `configuration/historical-baseline-state-batch-v1.json`, SHA-256 `6f16b548b1774f4fff9101c6f9ac6e8b1e9e08d014de1fef6ad4ffd13ec320e2` |
| Migration validator | `scripts/migrate_archive_envelopes.py`, SHA-256 `988fa540773860a391e40709df12774bde179e69b9e5c77ebc743978c59992c6` |
| Private plan builder | `scripts/prepare_historical_private_replay.py`, SHA-256 `2982dd857279fb4e76bca34fff178cbb8bf07950a77be252f0d158189431109e` |
| Public finalizer | `scripts/prepare_historical_public_authority.py`, SHA-256 `59e611fd468e700b766343adc2f3a861ed2fa3c182761ef2ba7f6efd66434d6b` |
| Migration infrastructure | Applied production template `infrastructure/aws-key-adapter/template.yaml`, SHA-256 `aac24318c973523a65b76af34b8e1408a5680f61b52c4fb996f93967253ef94d`. The completed operator procedure is not an execution input and must not be rerun. |
| Migration boundary | role `arn:aws:iam::161072922960:role/lean-eval-archive-migration-wrap-production`; environment `archive-migration-production`; environment variable `REVIEWED_IMPLEMENTATION_COMMIT`; review branch `archive-file-key-rewrap-v1`; confirmation `stage-envelope-migration` |
| Audit promotion contract | self-contained protected caller `.github/workflows/promote-archive-migration.yml`, SHA-256 `4ae6ebab493ce684392d848da04f931ff51cef3eefcd357c756f1cb745fba88a`; its promotion validator must remain byte-identical to audit contract commit `7a53c75c6d7c263c684ebcd54590c657c9298642`, whose reusable workflow SHA-256 is `b760ee6e6f04bcd061e3f15bea67c3dad33812e7cf5627b5850635d8228c8d3e` |
| Deterministic migration report | SHA-256 `faa26e1aa47eb629966db03695eda4f949b6c9804166f0047f53e09d9cc83339` |

The migration job's GitHub OIDC request handle is a job-level capability and
cannot be revoked between steps. Its AWS trust is restricted to the dedicated
production migration role, whose policy permits only `Encrypt` with the exact
v2 encryption context and never `Decrypt`. Before the audit writer token is
minted, the workflow removes the legacy decryption identity, active AWS
session, read-authority checkout, and migration scratch. Audit `main` remains
protected by the independently bound exact-patch promotion contract.

Protected-main review is the trust root for the workflow itself. The
environment-bound implementation commit prevents an operator typo or ordinary
Results-only intake churn from changing reviewed migration code; it does not
claim to defend against a malicious maintainer who first merges a workflow
that removes its own gate. Such a non-Results protected-main change fails the
existing gate and requires a new reviewed baseline before any later run.

Both replay controllers are restricted to protected `main`. Each lane has its
own non-cancelling concurrency group and admits at most one run; the bounded
driver can replenish one public and one private lane independently without
performing replay or holding State, Cloudflare, AWS, or audit credentials.
Each replay task allows at most four execution attempts and each job is bound
to 360 minutes. Cleanup has a seven-hour deadline and each OIDC token lifetime
is at most ten minutes. Public replay emits no artifact; the private controller
retains its source-free terminal receipt for 30 days.

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
- [x] The migration used reviewed implementation baseline
      `5f5f47b7c3c0670065dbd7c5ccb70e8802e3119c` from protected submissions
      `main`, bound as protected-environment variable
      `REVIEWED_IMPLEMENTATION_COMMIT` and passed as
      `expected_workflow_commit`. Each run self-binds its exact protected-`main`
      event SHA. A later event SHA is
      accepted only when the complete comparison from the reviewed baseline is
      non-truncated and contains solely added or modified `results/*.json`
      files; every other change fails before private audit checkout or any
      legacy-identity reference or use.
      Generate one unique `archive-migration-<32-lowercase-hex>` operation ID
      for exact dispatch correlation and crash-safe supervisor recovery.
      Require migration-workflow
      SHA-256
      `f1d00a45092a39afa0a0bc6be4cc9a319be85876e29a9ac20334f41f0aa0eb99`,
      private-controller SHA-256
      `402e8cab4e8d308cbca36ddcfe7060482a243b9c6aad29ba91b78a3dd1e672c8`,
      and public-controller SHA-256
      `a1afb9da60ddcd8bc7192d673b4cfc2e51831acc307164c252dee1a0b8126d21`.
      A successful dry run remains valid across only those proven Results-only
      descendants. Any other protected-`main` advance requires a new reviewed
      baseline and dry run before apply.
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
- [x] `REVIEWED_IMPLEMENTATION_COMMIT` in
      `archive-migration-production` had authenticated exact-value readback at
      `5f5f47b7c3c0670065dbd7c5ccb70e8802e3119c` before apply.
- [x] The exact migration inputs are audit commit
      `ad356e7bc5a2d650d9902ac3f6d352a0164360bc`, selected inventory digest
      `a8913f1c8b5073e5b7ab309ba10481b615ca4fc00e629e41a9e57962f3afebd4`,
      count 439, role
      `arn:aws:iam::161072922960:role/lean-eval-archive-migration-wrap-production`,
      environment `archive-migration-production`, isolated review branch
      `archive-file-key-rewrap-v1`, and apply confirmation
      `stage-envelope-migration`. The review branch is absent before apply.
- [x] Custodian Kim Morrison (`@kim-em`) derived the public key from the supplied
      legacy identity and required fingerprint
      `SHA256:4unwBywJxfq9LsOjygB+/NRHaXdBhvxKP+a3EEpqjoE` before bounded
      installation. The private material and path were not recorded, and
      `LEGACY_ARCHIVE_IDENTITY` is no longer installed.
- [x] Controller source commit
      `ffe2a6a2707136e667f1cb843098a2d2b00c716e` binds private-controller
      SHA-256 `c58077160ddd969b482867057c1b6404e2370bb99f4b6b3aeb665744bf9e70f6`
      and public-controller SHA-256
      `7d3d1b5c1a231d73db186331dbc6c52ca992e470a1fefbb5588aa0882fe14a74`,
      plus bounded driver SHA-256
      `5ce9e738e66b9ba3f1dcc30eef2264b9aa720d01a772ce2a9370c7888a65ad21`.
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
      `/api/v1/historical-private-replay/cleanup`. Each lane uses independent
      non-cancelling serialization, a seven-hour recovery lease, 360-minute
      jobs, four attempts, and explicit terminal dispositions.
- [x] Public projection v6 exposes only historical identity, visibility,
      profile/measurement digests, transition time, and terminal replay or
      unavailability fields. It redacts private archive, crosswalk, authority,
      and key-envelope locators. Private State intentionally retains exact
      content-addressed encrypted locators and digests; migration/replay does
      not modify Results, and no source, key material, or plaintext enters a
      public projection, artifact, log, release, or publication.
- [x] The retained-baseline migration completed with no retained job-local
      identity, AWS session, plaintext, or scratch. Exact staged audit commit
      `ef1f4b5c3cd1ddf543056f159fd1301334658e80` has tree
      `0be1d8df679d287169771ac269b28e5c2a42d2bf`; its canonical patch SHA-256 is
      `81537a9f9b0838fc043b402fc28b1a7510552b15a2a61b33f25ec1f0d221f699`.
      Promotion applied that patch to audit head
      `9c5c64644d7b3abd956c0e5aa658cb8f8cd2a9e7` and produced protected audit
      `main` `d73132415738b0d82c99fd43f630804fe996e342`, tree
      `48c24fc428eea77d7d9320133fd978f8c7b6abfc`. The review branch and installed
      legacy identity are absent. The production replay role and Cloudflare
      credential are installed, while both historical replay flags remain
      false and protected production State
      `3e4342b54252ba7225ced558c94ad0f03acc845d` has no active replay series.
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
real migrated private replay remains a single non-replenishing Cloudflare
execution canary; only after it reaches a reviewed terminal state may the
independent public and private lanes be replenished in parallel. The packet
binds the profile descriptor set instead of copying those fields into a second
format.

The retained-baseline migration portion of this section is complete. The
identity value was never part of the packet and is no longer installed; the
offline master remains retained only for the separately bound final-cutoff
delta.

## Post-migration readback and State bindings

The archive migration and promotion fields are fixed above. The State
candidate is bound by
`configuration/historical-baseline-state-promotion-v1.json`, SHA-256
`be88ae147e71195cd941cac1ab7b9d60c4adf97ba8fdd9113baf4130a02c4d2f`.

- [x] The deterministic migration report digest plus the staged audit commit,
      tree, and exact binary patch digest relative to the pinned audit source.
      Require 439 schema-version-3 sidecars, zero changed ciphertexts and stable
      IDs, and no retained plaintext, legacy identity, AWS session, or scratch
      output.
- [x] Immediately before promotion, bind the then-current audit `main`. Require
      the pinned source to be its ancestor and require zero overlap between all
      intervening changes and the migration-touched source and target paths.
      Apply exactly the staged patch to that current head, bind the resulting
      commit and tree, and promote only that rebased tree. After merge, require
      audit `main` to have exactly the bound tree.
- [x] The exact production State head used for combined validation is
      `60f5676e44a39010f5bc1fbfc4bd0bc228ff8028`, tree
      `97f31e6e0faa3c7029a31aa0cec057bf7be9d64b`, with 508 base events and
      event-ID-set SHA-256
      `8fb4df01a769eaba9c3af691cb1c41231b2c248dda4ec43cf94b8bdc275368d8`.
- [x] `first_occurred_at` is `2026-09-03T02:36:40.777Z`, after the exact
      State head, and `last_occurred_at` is `2026-09-03T02:36:43.215Z`.
      Deterministic public and private event-ID-set SHA-256 values are
      `4c4066d1e6d54badd26a1096558274d2b7883ec4567442c81718eaf44fda581a`
      and
      `b54afd7477d6364d5efde84137e38fcbdebb24a7630d9823570e8684afcccba4`.
- [x] The create-only State candidate is commit
      `d4f4ab87e916e25d7e5a7cb15bbd71c48f33d3a4`, tree
      `6fe5e06168b5d93c5bd677bb1c11bee829ef5e40`, with 2,439 events and
      event-set SHA-256
      `b83812b753a2ac33101da34289c3813eb2267a9e42b8b79501bcc40f91c5c96f`.
      Its public lane has 522 events and 174 tasks; its private lane has 1,917
      events and 639 tasks. The respective queue SHA-256 values are
      `4ede4c4aef87dc31dd5801f9001ea38451d42312140fc6aafbb490ea02b42a0a`
      and
      `39854ced5013850be149b50e9a7f8d34c86040b2ad527f779cfc2f5077921b7e`;
      materialized-views SHA-256 is
      `6cb8aeee80d5019fb1a0c1e954cd39779068e0a56e28ccb9f9f387ace10f6bd3`.
- [x] The read-only redacted historical projection and series SHA-256 values
      are
      `f20ce2b0271b3a6357a954ed9dde0d1b2de692f89309ca2cc8ef8da43d31e4ac`
      and
      `9c2f4e8d5a1306ad85efdd2642ea601160a6cf11f952e44e55ad0364154421be`.
      The bound public/private Result-set and task-set digests prove that all
      813 queued baseline Results appear exactly once, while the reviewed
      unavailability set SHA-256
      `8a81f3cc77d7a9a36fc399f569b9e19be0c581af62f4a76afa411c4522bb9f2d`
      retains the terminal dispositions for all 459 public and 29 private
      unavailable Results.

## Packet-bound execution order

1. The protected private replay plan, 63 profile locators, bounded migration
   infrastructure, archive migration, exact-patch audit promotion, and
   credential cleanup are complete at the bindings above. Do not rebuild or
   requalify the completed profiles and do not reinstall the legacy identity.
2. Run `prepare_historical_public_authority.py finalize-batch` against its
   pinned, complete State contract checkout
   `0c943edde8a247b8670e10339b80fc65be6c0f33`; the finalizer derives the exact
   20 terminal exclusions from that validated ledger. The caller supplies the
   timestamp and event-ID seed, and the finalizer requires every derived event
   to follow the ledger's latest event. Run
   `prepare_historical_private_replay.py state-events --selection full
   --append-ready` against that exact current head with non-overlapping times.
   Validate both candidate sets together against the current head before
   committing either candidate. The one-shot State workflow stages all 2,439
   events and their exact missing deterministic operational indexes as one
   create-only commit on fixed private review branch
   `historical-baseline-state-v1` and emits one compact source-free canonical
   binding. A separate packet-only commit must install that exact canonical
   object at `configuration/historical-baseline-state-promotion-v1.json`,
   binding the exact State and audit heads and trees, staged commit and tree,
   complete event/task/Result set, queue, view, and redacted-projection digests
   before promotion is dispatched.
3. Re-derive the complete committed binding from the staged State tree. Advance
   State `main` only by non-force fast-forward when it still equals the exact
   staged parent; otherwise leave `main` unchanged and discard only the exact
   stale review branch before restaging. Delete the review branch only after
   exact promoted-main commit and tree readback. Then enable only the private
   lane for one non-replenishing migrated-envelope canary. After its terminal
   cleanup, enable and replenish both bounded lanes, drain both queues with
   bounded retries, and record a terminal
   replay or reviewed-unavailable disposition for every retained-baseline
   Result.
4. After the retained-baseline queues reach their reviewed terminal states,
   disable the historical replay controllers. Retain the dedicated migration
   Encrypt role and stack output, protected migration environment, one-shot
   migration workflow, and custodian-held offline master for the separately
   bound final-cutoff delta.
5. After the announced issue-intake cutoff, generate the append-only delta and
   prepare a new exact packet covering only those added Results. Never extend
   this retained-baseline packet by implication. Use that packet to migrate any
   selected delta archives, apply its exact staged patch to the separately
   bound current audit head, promote the resulting tree, and complete its
   post-migration readback.
6. Only after final-delta promotion and readback, remove the installed identity
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
