# Historical private archive crosswalk

Historical private results cannot enter the replay corpus merely because an
encrypted object exists. Each accepted result must first be bound to the one
archive that preserved its exact recorded submission identity. The join needs
private repository metadata, but that metadata must not enter a public
artifact.

`scripts/classify_historical_private_archives.py` performs that join inside a
checkout that can read the private audit sidecars. It validates the complete
schema-version-2 results store through the shared result validator. Each result
file is read once into an immutable byte buffer; the exact parsed objects used
for the private join also produce the canonical results-store digest, which
must equal the independently reviewed pin. Each archive sidecar is likewise
parsed and hashed from one byte buffer. The classifier rejects schema hybrids,
orphan ciphertexts, and any sidecar—including retained schema-version-3
objects—that fails its independent finalized-sidecar validation. It also
rederives the complete archive-envelope migration plan and refuses a supplied
plan unless it is byte-for-byte equivalent. The command requires the results
subtree and complete audit checkout to be clean Git input at the claimed
commits. Operator-supplied result and archive digests, commits, and counts must
also agree. Dependency failures crossing this private boundary are reduced to
closed messages so private locators or values cannot enter logs.

For legacy issue intake, the equality key is the archive contract's immutable
recorded-submission identity:

```text
casefold(submitter), issue number, submission kind,
casefold(repository), exact source commit
```

For server intake, the UUIDv7 replaces the issue number. A unique candidate is
bound only when it is private, its declared model agrees, and any recorded
problem/verdict evidence contains a passing verdict for the accepted problem.
A historical sidecar written before problem-level evidence was introduced may
still be source-bound; the output marks that weaker evidence explicitly.
The archive benchmark commit is also compared. A difference is surfaced but
does not break the source binding because an idempotently retained source
archive may later be evaluated against another benchmark commit.

The output is governed by
`schemas/historical-private-archive-crosswalk-v1.schema.json`. It contains only
public result IDs, migration submission UUIDs, domain-separated archive-plan
entry commitments, schema/evidence classifications, the results/audit snapshot
commits and aggregate digests. It never contains submitter names, private
source repository names or commits, issue numbers, problem IDs, archive paths,
legacy ciphertext digests, or plaintext evidence.

## Classifications

- `bound` means one exact source identity exists and all available metadata is
  consistent. It is not itself replay authorization.
- `archive_not_found` means no exact archive identity exists in the pinned
  inventory. This is a pending orphan classification, not permanent
  unavailability.
- `archive_identity_ambiguous` means multiple archives claim the exact same
  recorded-submission identity. The tool reports only the candidate count and
  refuses to choose one.
- `archive_metadata_conflict` means the unique exact-identity candidate
  contradicts result metadata. The closed reason code is emitted without the
  conflicting private value.

## Read-only invocation

First generate the archive migration plan from the same immutable audit
checkout. Then run the crosswalk with independently reviewed pins:

```bash
python scripts/classify_historical_private_archives.py \
  --results-root /path/to/submissions/results \
  --results-commit <exact-results-commit> \
  --expected-results-store-sha256 <reviewed-results-digest> \
  --expected-private-result-count <reviewed-count> \
  --audit-root /path/to/exact-private-audit-checkout \
  --audit-commit <exact-audit-commit> \
  --archive-plan /private/path/archive-migration-plan.json \
  --expected-archive-inventory-digest <reviewed-plan-digest> \
  --output /new/path/private-archive-crosswalk.json
```

The output path must not already exist. Run the command twice to different new
paths and require byte identity before review. A protected workflow that later
publishes or commits the source-free artifact must independently validate its
JSON schema, exact counts, ordering, canonical bytes, source commits, input
digests, output SHA-256, and absence of private locator keys. The workflow must
not upload the audit checkout, migration plan, sidecars, ciphertext, or logs
containing join values.

## Retained canonical crosswalk

The reviewed source-free crosswalk is stored at
[`../evidence/historical-replay/private-crosswalks/dfdcbc0da3a3526f8a26e6a69cefa41cbcd92de7608752193b742fcd92b00a67.json`](../evidence/historical-replay/private-crosswalks/dfdcbc0da3a3526f8a26e6a69cefa41cbcd92de7608752193b742fcd92b00a67.json).
It binds results commit `7fb2e762e5470ae1929dbe069dbcd0c8488b51d7`
and store digest
`9e998ab47ae719484e2ea283271086d2c66c95051837231014fd74392f4fb1c0`
to audit commit `ad356e7bc5a2d650d9902ac3f6d352a0164360bc` and inventory
digest
`6b8867f41a13c3ba323746988058886e5dc73da7b509deaf01ccf9c36fe8d5d4`.
It covers all 668 private results: 639 are bound and 29 are explicitly
`archive_not_found`, with no ambiguity or metadata conflict. The missing
archives remain pending; this artifact does not authorize migration, replay,
or a State change.

The filename is the exact SHA-256 of the canonical bytes. CI validates the
schema, canonical encoding, ordering, complete private-result coverage from the
frozen results commit, reviewed counts and digests, and absence of private
locator keys.
