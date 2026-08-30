# Historical archive file-key rewrap

Historical migration is limited to the 439 unique schema-version-1 archives
bound to accepted private results by the canonical crosswalk at
[`dfdcbc0d…json`](../evidence/historical-replay/private-crosswalks/dfdcbc0da3a3526f8a26e6a69cefa41cbcd92de7608752193b742fcd92b00a67.json).
It does not migrate unbound archives, the legacy schema-version-2 object, or
existing schema-version-3 objects.

For each selected archive, the migration:

1. rederives the full archive plan from audit commit
   `ad356e7bc5a2d650d9902ac3f6d352a0164360bc`;
2. verifies the canonical crosswalk digest, audit inventory digest, 639 bound
   result rows, and 439 unique schema-version-1 plan-entry digests;
3. uses the pinned `filippo.io/age` v1.3.1 detached-header APIs to recover only
   the archive's 16-byte age file key;
4. wraps that file key with the version-2 provider adapter context;
5. copies the original `.tar.age` bytes to the stable UUIDv7 path; and
6. writes a schema-version-3 sidecar containing a version-2
   `age-file-key-v1` envelope.

The final validator independently rereads every pinned source object and
sidecar. It requires equal ciphertext SHA-256 and size, performs a bounded
chunk-by-chunk comparison of source and target ciphertext, and requires the
target sidecar to contain exactly the source's permitted preserved metadata
plus the intended schema-version-3 envelope fields. The plaintext digest and
size and stable submission ID must also remain unchanged. Migration never
decrypts an archive payload and never validates plaintext outside the replay
sandbox.

The protected workflow is manual and dry by default. Apply requires the exact
audit commit, exact reviewed workflow commit, selected-plan digest, count
`439`, confirmation text
`stage-envelope-migration`, the historical RSA identity, the Encrypt-only
migration role, and audit-repository credentials. It stages a normal review
branch named `archive-file-key-rewrap-v1`; it does not update audit `main`.
Before dependency installation or credential exposure, apply proves that the
review branch is absent, fetches current audit `main`, and refuses any drift in
the 439 selected source/sidecar or target/sidecar paths. A failed run is
retryable only while the review branch is still absent; an existing branch or
an inconclusive remote lookup is a hard stop.

The sole old recipient is maintained by Kim Morrison (`@kim-em`), GitHub SSH
key id `125797072`. Its expected SSH RSA 2048 public-key fingerprint is
`SHA256:4unwBywJxfq9LsOjygB+/NRHaXdBhvxKP+a3EEpqjoE`. Before installing the
secret, the custodian must derive the public key from the supplied unencrypted
private key and require that exact fingerprint:

```bash
set -o pipefail
ssh-keygen -y -f "$LEGACY_IDENTITY_FILE" \
  | ssh-keygen -lf - -E sha256
```

Do not record the private-key path or material. Install it directly into the
protected environment only after the pre-mutation packet is complete, and
remove the environment secret immediately after the bounded workflow run.

Promotion uses only
`scripts/promote_archive_envelope_migration.py`. Its `plan` command binds the
staged commit and tree, the full-index binary patch SHA-256, and the
then-current audit `main`. It requires the staged commit to be the sole direct
child of the pinned source, requires its changed paths to equal all and only
the migration-touched paths, proves source ancestry and zero intervening
overlap, and computes the exact rebased result tree in a temporary Git index.
Its `prepare` command rechecks both remote refs and every binding, then creates
one local `archive-file-key-rewrap-v1-promotion` worktree and commit with the
bound parent and tree. It never pushes or merges. After the reviewed candidate
is merged, `readback` requires the exact remote `main` commit, requires the
candidate to be its ancestor, and requires `main` to have exactly the promoted
tree. Any movement of either remote ref requires a fresh binding; do not rebase
or repair the generated candidate by hand. The same exact-patch/current-head
rule applies to the separately packeted final issue-intake delta.
The reviewed template must first be applied, through the immutable historical
migration/replay readiness packet, to every stack that must wrap or replay
these archives. Standing authorization covers the infrastructure mutation but
does not waive the packet, preflight, or rollback checks. The template adds
only exact-context v2 Encrypt authority to the production migration role,
which has no v1 Encrypt path, and exact-context v2 Decrypt authority to the
relevant unwrap function role. It does not change either live v1 statement.
Applying this template is distinct from connecting the production live-intake
Wrap role and from approving release
controller trust. The repository workflow does not apply the stack or enable
production capability.
The production operation is packaged in
[`aws-production-archive-migration-infrastructure.md`](aws-production-archive-migration-infrastructure.md);
it neither installs the legacy identity nor dispatches this workflow.

## Bounded promotion and readback

Use one private operator scratch directory and clean audit checkouts. First
regenerate the canonical plan from the pinned source checkout; neither this
plan, the binary patch, nor the following reports are committed to the public
submissions repository:

```bash
python scripts/migrate_archive_envelopes.py inventory \
  --audit-root "$PINNED_AUDIT_ROOT" \
  --source-commit ad356e7bc5a2d650d9902ac3f6d352a0164360bc \
  --crosswalk evidence/historical-replay/private-crosswalks/dfdcbc0da3a3526f8a26e6a69cefa41cbcd92de7608752193b742fcd92b00a67.json \
  --output "$OPERATOR_SCRATCH/migration-plan.json"
```

After the protected workflow has staged its fixed review branch, independently
bind the observed staged commit, staged tree, full-index patch digest, and
current audit `main`. Pass those exact values to the helper; it fetches and
rechecks both remote refs itself:

```bash
git -C "$CURRENT_AUDIT_ROOT" fetch --no-tags --force origin \
  +refs/heads/archive-file-key-rewrap-v1:refs/operator/archive-file-key-rewrap-v1 \
  +refs/heads/main:refs/operator/main
STAGED_COMMIT=$(git -C "$CURRENT_AUDIT_ROOT" rev-parse \
  refs/operator/archive-file-key-rewrap-v1)
STAGED_TREE=$(git -C "$CURRENT_AUDIT_ROOT" rev-parse \
  "$STAGED_COMMIT^{tree}")
PATCH_SHA256=$(git -C "$CURRENT_AUDIT_ROOT" diff --binary --full-index \
  --no-renames ad356e7bc5a2d650d9902ac3f6d352a0164360bc \
  "$STAGED_COMMIT" -- | sha256sum | cut -d' ' -f1)
AUDIT_MAIN_COMMIT=$(git -C "$CURRENT_AUDIT_ROOT" rev-parse refs/operator/main)

python scripts/promote_archive_envelope_migration.py plan \
  --audit-root "$CURRENT_AUDIT_ROOT" \
  --migration-plan "$OPERATOR_SCRATCH/migration-plan.json" \
  --staged-commit "$STAGED_COMMIT" \
  --expected-staged-tree "$STAGED_TREE" \
  --expected-patch-sha256 "$PATCH_SHA256" \
  --current-main-commit "$AUDIT_MAIN_COMMIT" \
  --output-patch "$OPERATOR_SCRATCH/migration.patch" \
  --output-binding "$OPERATOR_SCRATCH/promotion-binding.json"
PROMOTION_BINDING_SHA256=$(sha256sum \
  "$OPERATOR_SCRATCH/promotion-binding.json" | cut -d' ' -f1)
```

Record the binding and its digest in the packet before preparing a candidate.
The timestamp is another packet value and is not chosen implicitly:

```bash
python scripts/promote_archive_envelope_migration.py prepare \
  --audit-root "$CURRENT_AUDIT_ROOT" \
  --migration-plan "$OPERATOR_SCRATCH/migration-plan.json" \
  --binding "$OPERATOR_SCRATCH/promotion-binding.json" \
  --expected-binding-sha256 "$PROMOTION_BINDING_SHA256" \
  --patch "$OPERATOR_SCRATCH/migration.patch" \
  --output-worktree "$PROMOTION_WORKTREE" \
  --commit-timestamp "$PROMOTION_COMMIT_TIMESTAMP" \
  --output "$OPERATOR_SCRATCH/promotion-candidate.json"
PROMOTION_CANDIDATE_SHA256=$(sha256sum \
  "$OPERATOR_SCRATCH/promotion-candidate.json" | cut -d' ' -f1)
```

Review the generated commit, push only its fixed promotion branch, and merge it
only while its bound parent remains audit `main`. After merge, bind the observed
`main` commit and perform the final readback:

```bash
python scripts/promote_archive_envelope_migration.py readback \
  --audit-root "$CURRENT_AUDIT_ROOT" \
  --candidate "$OPERATOR_SCRATCH/promotion-candidate.json" \
  --expected-candidate-sha256 "$PROMOTION_CANDIDATE_SHA256" \
  --expected-main-commit "$MERGED_AUDIT_MAIN_COMMIT" \
  --output "$OPERATOR_SCRATCH/promotion-readback.json"
```

Do not append State or enable replay until this command succeeds and its exact
readback is recorded in the post-migration packet.

Before baseline apply, prepare the paired retirement changes, but retain the
one-shot workflow, protected `archive-migration-production` environment, and
migration Encrypt role until the separately bound final-cutoff delta is
promoted and read back. After each bounded migration run, remove the installed
`LEGACY_ARCHIVE_IDENTITY` environment secret and all session credentials. The
custodian retains the only offline master strictly for the final-cutoff delta
and reinstalls it only for that bounded run.

After the final delta is promoted and read back, remove the installed identity,
one-shot workflow, protected migration environment and its migration-only
variable and credentials, and the production migration Encrypt role and stack
output. Also delete the one-shot promotion helper, its focused test, this
document's bounded-promotion commands, and its obsolete execution-packet helper
hash and checklist fields. Retain v2 Decrypt in the replay unwrap role and
retain the schema-3 file-key replay implementation. The live archiver App
credentials are shared with ordinary archive workflows and are not
migration-only retirement targets.

The legacy RSA stanza remains in each unchanged age header. Only after the
final cutoff delta is complete must the custodian destroy the offline master
and verify that no installed or working copy remains. Exact ciphertext
preservation cannot revoke access held by an already-copied old private key.

Replay unwraps either envelope variant through the same single-use capability
boundary. Version 1 supplies a native age identity. Version 2 supplies exactly
16 bytes of `age-file-key-v1` material. Only the disposable replay sandbox can
turn either material into archive plaintext, and it validates the recorded
plaintext digest and size before extracting the archive.

The protected State contract permits `release.scheduled` only as a child of a
current `result.recorded` event. Historical archive replay authority and replay
verdicts cannot enter that release queue. The v2 envelope is therefore a replay
contract in the current scope; making historical Results releasable later would
require a separately reviewed State change and matching v2 support in the
release controller.
