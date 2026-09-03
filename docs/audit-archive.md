# Audit archive

## Why

The submission workflow used to keep no copy of the source it
evaluated. Public-repo submissions can be re-fetched from their
upstream so long as that upstream exists, but private-repo submissions
exist only in the submitter's account; if the submitter deletes the
repo, rotates a tag, or otherwise rewrites history, the bytes the
benchmark scored are gone. That defeats post-hoc auditability: a
comparator regression, a soundness incident, or a research question
about an older proof has no recoverable artifact to examine.

The archive exists to make every evaluated submission recoverable
indefinitely, while keeping the source bytes inaccessible to anyone
outside a small maintainer set.

## Design

One encrypted tarball plus one unencrypted JSON sidecar are pushed to
[`leanprover/lean-eval-audit`](https://github.com/leanprover/lean-eval-audit)
per submission before evaluation begins.

```
audit/
  YYYY/
    MM/
      {submitter}-{issue}-{ref8}.tar.age   # age-encrypted gzipped tar of source
      {submitter}-{issue}-{ref8}.json      # sidecar (issue, submitter, model, provenance, digests)

archives/
  {uuid-prefix}/
    {submission-uuidv7}.tar.age            # server-intake ciphertext
    {submission-uuidv7}.json               # sidecar schema version 3, UUID-keyed
```

The first layout is retained for historical GitHub Issue submissions. Server
intake uses a canonical lowercase UUIDv7 and the second layout, where `uuid-prefix`
is the first two hexadecimal digits of the UUID with hyphens removed. The two
identity modes are deliberately unambiguous: a legacy metadata record has an
`issue_number`; a server metadata record has a `submission_id`; neither may
carry both.

The archive tarball and the evaluator's independent refetch resolve to the
same immutable source commit. Their gzip/tar bytes need not be reproducible;
the workflow compares frozen metadata containing the exact commit before any
untrusted Lean runs. Current server archives use
[`age`](https://github.com/FiloSottile/age) with one fresh identity per
submission and wrap that identity in the schema-version-3 provider-neutral KMS
envelope. Historical issue archives use the recipients listed in
[`.audit/recipients.txt`](../.audit/recipients.txt). The sidecar records the
SHA-256 of both plaintext-tar and ciphertext so an operator can verify
integrity at decrypt time (against the plaintext digest) and without decrypting
(against the ciphertext digest).
For submissions made after publication disclosure was added, it also
preserves the submitter's `solution_publication_status` and optional
`solution_publication_date` snapshot.

## Workflow integration

Three ordered pieces live inside the existing `submission.yml`:

1. **Trusted archive job.** It independently fetches the requested source and
   the current benchmark, freezes their exact identities, enforces the 10 MiB
   compressed-source cap, encrypts the source, and pushes the ciphertext and
   provenance sidecar directly to `lean-eval-audit`. It mints the narrowly
   scoped archiver App token only after encryption. It runs no submitted code
   and uploads no source or ciphertext transport artifact.
2. **State acknowledgement.** A source-free callback job records the verified
   immutable locator. Evaluation cannot begin until that acknowledgement
   succeeds.
3. **Independent evaluation fetch.** The evaluation job checks out the exact
   benchmark commit frozen by archive, independently refetches the source, and
   compares the deterministic metadata digest before running untrusted Lean.
   The source remains local to that runner. `record` remains gated on archive,
   evaluation, and the applicable State callbacks.

### Persistence and locator details

The push is **idempotent on the source**, which matters because a
   submission can be re-evaluated (e.g. after a benchmark toolchain bump)
   and neither the ciphertext nor the plaintext tar is reproducible —
   `age` picks a fresh file key per run, and gzip/tar packaging varies, so
   re-fetching the same source yields different bytes at the same
   `audit/YYYY/MM/{submitter}-{issue}-{ref8}` path. The script therefore
   decides on the **recorded submission identity** — the stable tuple
   `(submitter, issue, submission_repo, submission_ref)` in the sidecar.
   `submission_ref` is a 40-char git SHA, which immutably pins the source
   tree content, so no digest is needed to identify the source (and
   including the non-reproducible `sha256_plaintext_tar` would misclassify
   a legitimate re-evaluation as a collision). Before uploading it reads
   any existing sidecar: if the identity matches, this exact source is
   already archived, the push is a no-op, and the original (immutable)
   ciphertext is left untouched. A path collision with a *different*
   identity is an operator-investigatable collision and fails hard, not
   something to silently resolve.

   The per-file Contents API call is a create-or-update: it fetches any
   existing file's Git blob SHA and supplies it on the PUT, so a rerun
   that finds an orphan ciphertext (uploaded by a prior run that crashed
   before the sidecar) updates it in place rather than failing the
   create with the API's `"sha" wasn't supplied` 422. That 422 is only
   treated as a benign already-exists conflict when the body says so; any
   other 422 (malformed path, oversize content, branch protection) is a
   real validation failure and fails fast.

   For server intake, `push` additionally requires `--locator-output`. It
   writes a durable handoff governed by
   [`archive-locator-v1.schema.json`](../schemas/archive-locator-v1.schema.json).
   Its archive fields are the exact payload for
   State's `archive.completed` event; `submission_id` becomes that event's
   subject:

   ```json
   {
     "schema_version": 1,
     "submission_id": "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f",
     "archive_repository": "leanprover/lean-eval-audit",
     "archive_commit": "0123456789abcdef0123456789abcdef01234567",
     "archive_path": "archives/01/0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f.tar.age",
     "archive_ciphertext_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
     "encrypted": true
   }
   ```

   `archive_commit` is the immutable audit-repository commit containing the
   uploaded sidecar. Before emitting the locator, the archiver reads the
   ciphertext back at that exact commit and checks its SHA-256 against the
   sidecar. This makes a racing or partially completed pair fail closed rather
   than allowing State to record a locator whose bytes do not match its
   digest. An idempotent replay uses the existing sidecar's last-changing
   commit and performs the same byte check.

   `archive` exposes `audit_ciphertext_ready` as a job output, set to
   `'true'` only when encryption succeeded. Archive failures are reported to
   lifecycle State before evaluation is allowed to start.

## Threat model

The thing the design defends against is: **the source bytes of any
private submission leaking out of the maintainer set**.

Concretely:

- **Public-repo artifacts are downloadable by any authenticated user.**
  The workflow therefore forbids both plaintext source transport and the old
  ciphertext handoff artifact. Archive persists directly; evaluation refetches
  the frozen commit.
- **Runners that elaborate untrusted Lean can be compromised.** The
  archiver App's installation token must never appear in the env of
  any job that runs untrusted Lean. It is minted only in the trusted `archive`
  job. That runner necessarily handles the submitted bytes in order to encrypt
  them, but never executes them; the separate evaluation runner receives no
  archiver token.
- **App permission scoping.** `lean-eval-archiver` has `Contents:
  write` only on `leanprover/lean-eval-audit`, and is installed only
  on that repo. The pre-existing `lean-eval-bot` (which reads private
  submission repos) and `lean-eval-recorder` (which writes the
  leaderboard) keep their previous, narrower scopes — none of them is
  bumped to write to private submitter accounts or to audit.

## What is NOT in the threat model

- **Recipient-private-key custody is the recipient's problem.** Lose
  it and the corresponding archived entries become permanently
  undecryptable. The validate-recipients CI does not (and cannot)
  verify that someone holds each recipient's private key. The project accepts
  that loss risk and does not impose a recurring recovery drill.
- **Pre-existing public submissions and their commits.** Those exist
  in the submitter's own public repo at a pinned SHA, recorded in
  `results/<login>.json`. They are also archived going forward for
  symmetry and forensic completeness, but a deleted upstream remains
  primarily a problem for the submitter's own repo, not for the
  archive.
- **Anonymity.** Submissions are not anonymous in the leaderboard, in
  the issue, or in the sidecar. The archive's privacy guarantee is
  about *source bytes*, not about whether a given submitter
  participated.

## Adding or removing recipients

Open a PR that edits `.audit/recipients.txt`. The
`validate-recipients.yml` workflow lints each line by encrypting a
fixture to it; merging without the lint passing is impossible. Once
merged, every subsequent submission is encrypted to the new recipient
set. Pre-existing ciphertexts retain the recipient set they were
encrypted with — re-encrypting historical entries to a new recipient
is a manual operation requiring decryption first.

## Decryption procedure

```bash
# 1. Install age.
brew install age            # or: apt install age, or cargo install rage

# 2. Decrypt the ciphertext using one of the SSH private keys whose
#    public half is in recipients.txt.
age -d -i ~/.ssh/id_rsa \
  -o /tmp/source.tar.gz \
  audit/2026/05/GanjinZero-73-52c6d202.tar.age

# 3. Verify the recovered bytes against the sidecar's plaintext SHA.
sha256sum /tmp/source.tar.gz   # match sidecar.sha256_plaintext_tar

# 4. Extract.
mkdir /tmp/source && tar -xzf /tmp/source.tar.gz -C /tmp/source
```

## Size cap

The 10 MiB cap is on the **compressed gzipped tar** of the
post-`.git`-strip source tree, which is what the workflow uploads
and what `du -h` will show for a typical generated workspace plus a
modest Submission/ directory (well under 1 MiB for current
submissions). The cap exists because the archive is permanent and
public-repo-shaped (one file per submission, committed forever) and
because nothing in the current submission shape needs more space; if
a use case for >10 MiB submissions emerges, we bump the cap rather
than special-case some submissions.

A submission over the cap is rejected at the workflow level: the
issue is commented and closed, no evaluation is run, no leaderboard
update happens, and no audit entry is created.

## Backfill

A one-off script (`scripts/backfill_audit.py`) walks every entry in
`results/*.json`, re-fetches the submission via `lean-eval-bot`, and
archives it the same way the live workflow would have. Public-repo
submissions are best-effort: if the upstream has been deleted or
rewritten, the entry is logged and skipped. Private-repo submissions
where the bot has lost access fall into the same bucket. The
backfill is idempotent — entries that already exist in the audit
repo are not re-uploaded.
