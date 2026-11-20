# File-key migration for four retained public archives

The finite adapter prepares four schema1 archives at Audit commit
`f3df4480d48cf5024602c15d656e4469fa774cc9`, covering five original public
Results, for the existing schema3 file-key replay path. It preserves the original
public visibility and exact encrypted bytes. It does not recover source from Git,
change Results or State, create recovery authority, alter attempts, or activate
replay.

The separate caller
[`migrate-queued-public-archive-envelopes.yml`](../.github/workflows/migrate-queued-public-archive-envelopes.yml)
is manual and dry by default. The existing439-archive workflow, canonical
selection guard, constants and original API remain unchanged. Both old APIs
still run their original guard before the shared transformation/validation
bodies. The finite adapter has its own closed selection and cannot make the
old caller accept four archives.

The content-addressed source selection is
[`47a6d80a…json`](../evidence/historical-replay/archive-migration-selections/47a6d80ac52799d2eb55be4846b56869d9bea7c1cf92aac2814bdbc3c0827d79.json).
It binds five original Result associations and references the exact existing
producer plan at `archive-migration-plans/0e86c85775bbe111e3e8b8adda4ac987b6f97688149e2e01df27d9256e376378.json`.
That standalone immutable plan blob is reused directly by the downstream
archive-crosswalk-v2 builder. The plan inventory digest is
`260dd44bab488046530ef5bb8ae647c2d38e6ec98db1402ceb70a96d848a18ff`:
four unique schema1 entries, zero retained entries and no extras. Actual entry
hashes retain the existing producer's domain and canonical encoding; a
metadata-only normalized digest is not migration authority. The four verified
ciphertexts total487199bytes and fit the existing small-archive class.

Before any legacy identity or AWS authority, the adapter verifies the selection
file hash/canonical bytes, exact original authority Git blobs and canonical
public plan schema, all original Results Git bytes/tree commitments and record
identities, and every original-public source/benchmark/workflow field through
the shared identity-only helper. No old execution profile is fabricated.
Original source associations are proved before migration; target-side v2
crosswalk/recovery artifacts cannot be prerequisites because their future
sidecar/envelope hashes do not exist yet.

Inventory reads only the exact eight pinned regular Git objects into private
scratch, verifies their SHA commitments, and rederives the actual producer
plan. It rejects missing/extra objects, symlinks, changed source/UUID/schema,
non-public archives and source/Result provenance drift. The resulting plan
must equal the closed source selection. An additional source reproof runs
immediately before the migration role is assumed.

Apply uses the existing pinned detached-header age helper and Encrypt-only v2
adapter. It never decrypts archive payloads. The shared producer copies the
original ciphertext exactly to the stable UUID path and preserves the allowed
metadata, including `submission_public: true`, claimed tar SHA/size and recorded
benchmark evidence. Only the existing schema3/v2 envelope fields are introduced.
The final shared validator independently checks exact pair inventory, complete
ciphertext byte equality/size/digest, UUID, v2 envelope binding and the exact
preserved metadata field set. Plaintext is validated only by a separately
qualified disposable replay sandbox.

The caller uses the existing noncancelling migration concurrency group and
fixed isolated `archive-file-key-rewrap-v1` review branch. Before dependency
installation or legacy/AWS authority it must prove the branch absent, source
ancestry and zero overlap with current Audit main across all16 touched paths.
Any existing/inconclusive branch or overlapping source/target pair is refusal.
It does not overwrite a prior branch or push Audit main.

After successful validation, it destroys temporary legacy identity, AWS/OIDC
session material and the source read checkout before minting an audit-only
writer. The staged commit deletes exactly four old pairs and adds exactly four
UUID pairs, preserving the global archive count. Promotion remains the
unchanged existing caller: reviewed count4, exact source/staged commit/tree,
canonical binary patch digest, freshly bound Audit main, zero overlap and exact
deterministic result commit/tree. CAS push and protected-main readback precede
review-branch deletion. Unrelated intake history is preserved; overlapping
history is never automatically repaired.

The final promoted Git tree and existing producer plan supply the target
sidecar hash, canonical provider-neutral envelope hash, actual ciphertext
size/SHA and stable UUID paths to the separate archive-crosswalk-v2 builder.
That builder retains original observed schema1/public evidence and binds target
schema3 execution. Four migrations cover five distinct Results without merging
their identities. No duplicate migration authority manifest or State producer
is introduced here.

Implementation and dry-run review do not grant permission to install a legacy
identity, assume AWS, apply, or promote. The existing infrastructure/custodian
handoff and immutable execution packet remain mandatory. This adapter requires
the shared original-public identity consumer prerequisite (PR2209). A separately
reviewed operation must verify exact current protected source, IAM context,
branch absence, source selection and cleanup before any bounded credential use;
no real key or migration is needed to run the offline tests.
