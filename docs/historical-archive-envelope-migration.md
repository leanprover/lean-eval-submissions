# Historical archive envelope migration

The one-time migration replaces every schema-version-1 or schema-version-2
shared-recipient ciphertext with a fresh per-submission age archive and a
schema-version-3 KMS envelope. Existing schema-version-3 objects are copied
byte-for-byte. The source audit commit is never edited in place.

The planner verifies every ciphertext/sidecar pair and freezes a source-free
manifest containing the old ciphertext and sidecar digests, plaintext
digest/size evidence, deterministic submission UUIDv7, and canonical target
path. A pre-server schema-version-1 object receives a stable UUIDv7 whose
timestamp comes from `archived_at` and whose random bits are a domain-separated
hash of its immutable locator and ciphertext digest. The one schema-version-2
server object keeps its existing submission ID.

```bash
python scripts/migrate_archive_envelopes.py inventory \
  --audit-root /path/to/exact/audit-checkout \
  --source-commit <exact-40-character-commit> \
  --output /private/path/migration-plan.json
```

The first credentialed snapshot, at audit commit
`92b95c162ad9bf38d027e11193683ca61ed2a994`, contained 1,042
ciphertext/sidecar pairs: 1,040 requiring migration and two already-current
schema-version-3 objects retained byte-for-byte. Dry run `32616816083`
reproduced its canonical inventory digest
`48f55807f430d8754e4a7b79cb391d582028df6abce347d037bd810a0e3decfa`
with every decrypt, wrap, and write step skipped.

The current protected snapshot, at audit commit
`ad356e7bc5a2d650d9902ac3f6d352a0164360bc`, contains 1,045 pairs:
1,043 requiring migration and two retained schema-version-3 objects. Protected
dry run `32840226134`, from submissions commit
`fc761446bb71d655a7cbab1d76d2ea7fc1cad898`, reproduced the exact count and
canonical digest
`6b8867f41a13c3ba323746988058886e5dc73da7b509deaf01ccf9c36fe8d5d4`.
It used only the audit-repository read credential: no legacy identity, AWS
role, plaintext, artifact, branch, or audit write was used. Intake changes the
source commit and can change the inventory; operators must therefore freeze
and review fresh counts and a fresh digest immediately before apply.

The migration plan alone cannot correlate an accepted private result with an
archive because it intentionally omits repository and ref metadata. Before
replay planning, run the deterministic protected-input classifier described in
[`historical-private-archive-crosswalk.md`](historical-private-archive-crosswalk.md).
It rederives and verifies this complete plan, joins results to raw sidecar
metadata only in memory, and emits no source or legacy archive locator. A
unique source binding is necessary but is not replay authorization.

`Migrate historical archive envelopes` is the protected operator path. A dry
run is the default and needs no legacy identity. Apply additionally requires:

- the exact expected audit commit, migration count, and freshly reviewed
  inventory digest;
- confirmation text `stage-envelope-migration`;
- `LEGACY_ARCHIVE_IDENTITY`, the RSA identity matching the historical
  recipient, in `archive-migration-production`;
- `AUDIT_MIGRATION_READ_KEY`, a read-only deploy key scoped only to the audit
  repository (the write-capable App is minted only after apply confirmation);
- `AWS_WRAP_ROLE_ARN`, set to the production stack's
  `MigrationWrapRoleArn` output. That dedicated role trusts only the exact
  `repo:leanprover/lean-eval-submissions:environment:archive-migration-production`
  OIDC subject and grants only KMS Encrypt on the production archive identity
  key with the complete archive encryption context. The ordinary production
  `WrapRoleArn` trusts `archive-production` instead and is incompatible with
  this workflow; and
- the existing audit-writer GitHub App, restricted to the private audit repo.

The apply workflow deliberately never overlaps the audit write token with
decrypt or wrap authority. It validates the inventory and installs pinned
tools before assuming the Encrypt-only role. The re-encryption step installs
its cleanup trap before materializing the legacy identity, and that same step
removes the identity and clears its local AWS session and OIDC request
variables before it can finish. A separate fail-closed step proves those local
credential and request variables are unavailable before the workflow mints the
audit-repository-only App token. It also removes the read-only source checkout
before minting that token. The writer phase therefore retains only the
validated schema-version-3 ciphertext tree and source-free plan/report, then
checks out the exact audit source needed to create the orphan review branch; it
cannot see a legacy identity, plaintext, or AWS session variables. Every
writer-phase step explicitly overrides the OIDC request and AWS session
variables with empty values. The writer checkout persists no credential. The
App token is passed transiently to that checkout, then the push step moves it
immediately into an unexported shell variable. Only the derived authorization
header is exposed to the single push through command-local `GIT_CONFIG_*`
environment variables guarded by an exit trap; neither checkout nor push
writes the credential to Git configuration or places it in a command-line
argument.

For every entry the job decrypts into runner scratch, verifies the historical
plaintext digest and size, creates a fresh post-quantum age identity, wraps
only that identity through the KMS adapter, and immediately discards plaintext
and identities. The final validator requires exactly one schema-version-3
sidecar and one ciphertext for every planned target, unchanged retained
objects, preserved plaintext evidence, new ciphertext bytes, and no legacy
locator in the replacement tree.

Apply only stages an orphan audit branch named
`archive-envelope-migration-v1`; it does not update or force-push `main`.
Review the branch commit and source-free validation report independently.
Promoting that clean tree and expiring the historical shared identity is a
separate destructive authorization point because retaining old Git history
would retain every shared-recipient ciphertext.

The historical RSA private key is not present in the Codex workspace as of
2026-08-23. Do not substitute another key, weaken digest checks, retain
plaintext as an artifact, or claim the migration has run until the matching
identity is supplied by its custodian.
