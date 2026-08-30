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
| Private audit source | audit commit `ad356e7bc5a2d650d9902ac3f6d352a0164360bc`, inventory digest `6b8867f41a13c3ba323746988058886e5dc73da7b509deaf01ccf9c36fe8d5d4` | 1,045 archives |
| Selected schema-1 migration set | inventory digest `a8913f1c8b5073e5b7ab309ba10481b615ca4fc00e629e41a9e57962f3afebd4` | 439 unique archives |

The fixed reviewed implementation bindings are:

| Component | Exact binding |
| --- | --- |
| Migration workflow | `.github/workflows/migrate-archive-envelopes.yml`, SHA-256 `242da58b04478f7bf614f55b6ab78d01cd494b5b6df867681e2e5616ef848f64` |
| Private replay controller | `.github/workflows/historical-private-replay.yml`, SHA-256 `b0b2e1310ce3e71ee2738439a9d3c371259d976dc3a0556963072563f66eb76c` |
| Public replay controller | `.github/workflows/historical-authoritative-replay.yml`, SHA-256 `4f8572f27d9e1c9d8013059135c7446b905cbda16226f5c7cd38ec2f65296a6e` |
| Migration validator | `scripts/migrate_archive_envelopes.py`, SHA-256 `988fa540773860a391e40709df12774bde179e69b9e5c77ebc743978c59992c6` |
| Private plan builder | `scripts/prepare_historical_private_replay.py`, SHA-256 `2f1ae6a6e8710a0d0983aa7c2b3f64e77ebf2322da8154c04e39be084f4355e4` |
| Public finalizer | `scripts/prepare_historical_public_authority.py`, SHA-256 `2b5c4e6d2a88c3f4d703de17889476f18d9b2922c2287da7918ae08c7600fdda` |
| Migration infrastructure | `infrastructure/aws-key-adapter/template.yaml`, SHA-256 `f075b0439dfadd83930cb18051e595fa6d378b3a5e30c55cb2f1966e6820a45a`; operator script SHA-256 `bee43ece436377c5eb5ec717b3985ab669ef2662dd92dedcacc7039edd0c5d7a` |
| Migration boundary | role `arn:aws:iam::161072922960:role/lean-eval-archive-migration-wrap-production`; environment `archive-migration-production`; review branch `archive-file-key-rewrap-v1`; confirmation `stage-envelope-migration` |
| Deterministic migration report | SHA-256 `faa26e1aa47eb629966db03695eda4f949b6c9804166f0047f53e09d9cc83339` |

Both replay controllers are restricted to protected `main`, use separate
non-cancelling concurrency groups, allow at most four execution attempts, and
bound each job to 360 minutes. Cleanup has a seven-hour deadline, receipts are
retained for 24 hours, and each OIDC token lifetime is at most ten minutes.

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

- [ ] One submissions commit containing all 63 canonical private execution
      profile files produced by immutable image build, publication readback,
      and offline inspection. Bind the canonical descriptor set of exactly
      `{path, sha256}` and its SHA-256, using the same serialization as the
      public batch finalizer. Validate benchmark-commit and execution-profile
      identities separately; they are not descriptor-set hash fields.
- [ ] One regenerated private replay plan at its digest-derived path and one
      commit containing it. It must contain 63 profiles, 639
      `profile_qualified` bound entries, and the same 29
      `archive_not_found` entries.
- [ ] The exact reviewed submissions commit used by the migration workflow and
      historical replay controllers, plus the migration workflow blob digest.
- [ ] The production migration-infrastructure template digest and post-apply
      readback: dedicated migration Encrypt role, replay v2 Decrypt support,
      unchanged v1 statements, and the migration environment bound only to the
      dedicated role.
- [ ] The exact migration inputs: audit commit
      `ad356e7bc5a2d650d9902ac3f6d352a0164360bc`, selected inventory digest
      `a8913f1c8b5073e5b7ab309ba10481b615ca4fc00e629e41a9e57962f3afebd4`,
      count 439, dedicated role ARN, protected environment, isolated review
      branch name, and apply confirmation.
- [ ] The exact controller commits, immutable image manifest digests, OIDC
      subjects and route scopes, serialization/lease limits, four-attempt cap,
      and terminal-disposition rules for the later bounded replay.
- [ ] The redacted leaderboard projection fields and proof that no private
      locator, source, archive key, or plaintext can enter State, Results,
      artifacts, logs, or publication.
- [ ] Fail-closed recovery and rollback: leave audit `main` unchanged if
      migration validation fails; keep replay variables absent; remove the
      installed legacy identity, AWS session, scratch output, and review branch
      when the bounded operation fails. Retain the reviewed migration role,
      protected environment, and workflow for the separately bound final-cutoff
      delta unless the entire migration lane is explicitly abandoned and
      retired.
- [ ] Explicit exclusions: no legacy-key destruction, final-cutoff delta,
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

1. Build, publish, inspect, and commit the 63 private profile objects, then run
   `prepare_historical_private_replay.py plan` with all 63 paths and their one
   exact containing commit. Commit the digest-derived plan.
2. Complete the pre-mutation authorization bindings, apply and verify the
   bounded migration infrastructure, and only then have the custodian install
   the legacy identity for the exact protected workflow run.
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
   environment, dedicated migration Encrypt role and stack output, and
   temporary private image-build workflow. The custodian must then destroy the
   offline master and verify that no installed or working copy remains. Retain
   v2 replay Decrypt support, the schema-3 file-key replay implementation, and
   the versioned replay/checker records. Enable the historical controllers only
   for the separately reviewed final-delta queues and disable them again after
   every Result has a terminal disposition.

At every step, an input mismatch leaves the corresponding capability disabled.
Creating and filling this packet does not itself write State, migrate an
archive, or enable replay; only the separately invoked packet-bound execution
steps do so.
