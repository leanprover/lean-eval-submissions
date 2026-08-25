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
commits and aggregate digests. It never contains submitter names, repository
names, private submission source commits, issue numbers, problem IDs, archive
paths, legacy ciphertext digests, or plaintext evidence.

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

## Current diagnostic result

A local read-only run over submissions commit
`ae1a9714c5433b4c195b8fdfb5643893ecac8019` and audit commit
`92b95c162ad9bf38d027e11193683ca61ed2a994` reproduced results-store digest
`14e8c8682e5183d85fee32aafcf06eedb20d7cd8aa91d666d50753d516da7d43`
and archive inventory digest
`48f55807f430d8754e4a7b79cb391d582028df6abce347d037bd810a0e3decfa`.
It classified all 668 private accepted results as 639 `bound` and 29
`archive_not_found`, with zero ambiguous identities and zero metadata
conflicts. The source-free canonical bytes had SHA-256
`64a93054ac28379a7d5d5c5e4b00b2b14a7a99ccf6f6c294130a5b910499ea29`.

This local run is diagnostic evidence only. The output was not committed,
uploaded, written to State, used to decrypt an archive, or used to authorize a
replay. The 29 unmatched results remain pending archive recovery or a separate
reviewed unavailability policy.
