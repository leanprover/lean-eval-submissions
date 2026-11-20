# Retained public archive recovery preparation

This source prepares recovery of five already queued original public Results
from four retained prepared-source archives. It preserves each original public
authority, Result, task ID, benchmark, stock Lean toolchain, measurement config
and attempt counter. The archive is a prepared snapshot; its original Git
locator remains provenance and is never represented as a reconstructed tree.
There is no fallback to Git after archive recovery.

State must adopt the exact reviewed archive-v2 source contract at
`351b1c409290516bfa379fc857908908e1d08932` through the coordinated reader rollout.
Existing private-v1 requests, profiles and historical reconstruction remain
supported. This consumer refuses standalone archive-v2 admissions and the large
archive capacity class. Its only new executable capacity is `legacy_10mib`:
11 MiB ciphertext, 10 MiB tar, unchanged expanded-size/member limits.

## Evidence and local preparation

The new closed schemas describe archive crosswalks, recovery manifests, archive
profile qualifications, execution profiles and execution requests. Original
public authority proof is shared with the public controller. A migrated
schema-3 sidecar must preserve original public visibility and source metadata;
its ciphertext, exact v1 producer-plan entry, canonical Results and file-key
schema-2 envelope must match. No private archive authority is synthesized.

`prepare_historical_archive_recovery.py` consumes canonical prospective task
rows with State queue schema 2. These are preparation inputs, not a published
queue or State authority. Its three commands write create-only mode-0600 JSON:

- `crosswalk` renders a prospective crosswalk from explicit task rows, the
  canonical Results-store digest and immutable migration-plan locator.
- `manifest` renders the prospective recovery manifest. Commit and review the
  crosswalk first, then bind its actual Git commit/path/SHA in these rows.
- `events` requires the actual committed manifest, crosswalk, qualified profile,
  migration plan and migrated Audit target. It proves all their Git/source
  bindings, the frozen five-Result/four-archive selection, the exact unexecuted
  original protected queue, zero running historical tasks and the complete
  prospective State semantic graph before writing local event candidates.

The finite source-selection artifact has SHA-256
`47a6d80ac52799d2eb55be4846b56869d9bea7c1cf92aac2814bdbc3c0827d79`
and lives under `evidence/historical-replay/archive-migration-selections/`.
It binds the existing v1 migration plan and the five original public authority
associations. Candidate generation requires its reviewed immutable commit.
Preparation cannot supply an unqualified or invented image in place of the
actual committed archive qualification.

All three events per task must be added together in one reviewed State Git
commit: source reconfiguration, archive profile qualification, re-enqueue.
Candidate output does not append or promote anything. Review, consumer adoption,
zero-running controller pause, exact State Git commit proof and ordinary
protected promotion are separate mandatory steps. A shallow checkout or split
recovery commit is rejected, including historical reconstruction from an export.
No attempt reset or budget extension is implemented.

## Executor and finite images

Execution request 2 separates original `visibility: public` from
`fetch_method: archive`. Archive expectation 3 retains the existing file-key
material type and envelope 2; authoritative multipart transport remains 3.
The temporary executor config explicitly binds the small capacity class and
ownership verification requires matching health metadata. Existing private-v1
configs and requests keep their original forms.

The create-only finite image matrix contains the two exact retained Lean 4.33
benchmark families and is explicitly unqualified. The manual temporary image
workflow preserves stock toolchains and immutable component/source/registry
locks. Its future qualification includes pinned NanoDa positive and kernel
negative probes and the official archive entrypoint offline probe. It does not
create a Cloudflare executor. Runtime validation remains
`deferred_to_first_historical_replay`: each changed image needs its first real
State-admitted canary before its chain proceeds. Failure stops that image's
chain for diagnosis; it does not imply permanent source loss.

No image was built or published by this source preparation. The workflow's
publication confirmation and immutable dispatch/protected-main guards remain
required. Delete the temporary image workflow, qualification producer and
focused workflow tests after both profiles and their recovery readback pass.
The retained private 439-archive migration selection remains unchanged. The
finite four-archive migration uses its own reviewed bounded source adapter;
none of these commands decrypts, rewraps, submits, appends or changes live State.
