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

At audit commit `3ac5dfdcd9f8fde336775f194fe4e9fad1a182bc`, the
verified inventory contains 1,041 ciphertext/sidecar pairs: 1,039 schema
version 1, one schema version 2, and one already-current schema version 3.
Thus 1,040 objects require migration. The canonical inventory digest is
`12b2018ad7905d51523c10f5c987a7626d602da65226e169f4c0a7a6de02388e`.
Later schema-version-3 intake changes the retained count and source commit but
must not change the 1,040-entry migration set.

`Migrate historical archive envelopes` is the protected operator path. A dry
run is the default and needs no legacy identity. Apply additionally requires:

- the exact expected audit commit, migration count, and freshly reviewed
  inventory digest;
- confirmation text `stage-envelope-migration`;
- `LEGACY_ARCHIVE_IDENTITY`, the RSA identity matching the historical
  recipient, in `archive-migration-production`;
- `AUDIT_MIGRATION_READ_KEY`, a read-only deploy key scoped only to the audit
  repository (the write-capable App is minted only after apply confirmation);
- `AWS_WRAP_ROLE_ARN`, restricted to KMS Encrypt on the production archive
  identity key; and
- the existing audit-writer GitHub App, restricted to the private audit repo.

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
