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
per submission, immediately after evaluation.

```
audit/
  YYYY/
    MM/
      {submitter}-{issue}-{ref8}.tar.age   # age-encrypted gzipped tar of source
      {submitter}-{issue}-{ref8}.json      # sidecar (issue, submitter, model, digests, verdict)
```

The tarball is the same `source.tar.gz` that `fetch_submission.py`
already produces — the same bytes the evaluator sees. Encryption uses
[`age`](https://github.com/FiloSottile/age) with recipients listed in
[`.audit/recipients.txt`](../.audit/recipients.txt). The sidecar
records the SHA-256 of both plaintext-tar and ciphertext so an
operator can verify integrity at decrypt time (against the plaintext
digest) and without decrypting (against the ciphertext digest).

## Workflow integration

Two new pieces live inside the existing `submission.yml`:

1. **Size cap + encrypt**, in the `evaluate` job, right after fetch.
   `fetch_submission.py` writes `source.tar.gz` to `/tmp/fetch-out/`.
   The workflow rejects the submission if `source.tar.gz` exceeds 10
   MiB (comments on the issue, closes as `not planned`). Otherwise it
   shells `age --recipients-file .audit/recipients.txt` over the tar
   and uploads only the ciphertext as an artifact. The plaintext is
   read for evaluation in this same job but never crosses the
   job boundary.
2. **Archive job**, runs after `evaluate`. Mints an installation token
   for the `lean-eval-archiver` GitHub App (scoped only to
   `lean-eval-audit`), downloads the ciphertext + sidecar artifact,
   merges in the per-problem evaluator verdict from
   `summary.json["run_eval"]["problems"]` (the raw output of
   `lake exe lean-eval run-eval --json`), and uploads both objects via
   the GitHub Contents API. `record` (the leaderboard updater) is
   gated on this job succeeding: if archive fails, no leaderboard
   update happens.

   The Contents API upload is **idempotent**. A workflow rerun after a
   partial failure that uploaded the ciphertext but not the sidecar
   encounters the existing ciphertext at the predicted path; the
   script fetches the file's Git blob SHA and compares it with the
   blob SHA of the local bytes. On match, the existing upload is
   treated as success and the workflow proceeds to the sidecar upload.
   On mismatch, the push fails hard — two different submissions racing
   into the same `audit/YYYY/MM/{submitter}-{issue}-{ref8}` path is an
   operator-investigatable collision, not something to silently
   resolve.

   `evaluate` exposes `audit_ciphertext_ready` as a job output, set to
   `'true'` only when both the encrypt step and the ciphertext-artifact
   upload step succeeded. `archive` gates on this output; `notify`
   branches on it so an audit-encryption failure produces an
   "audit encryption failed" comment rather than the misleading
   "Submission.lean failed to compile" message that would otherwise
   fire from the generic evaluate-failure path.

## Threat model

The thing the design defends against is: **the source bytes of any
private submission leaking out of the maintainer set**.

Concretely:

- **Public-repo artifacts are downloadable by any authenticated user.**
  This is why the workflow already forbids `name: submission-source`
  (see `submission.yml`'s leading comment, and the
  `test_fetch_and_evaluate_share_one_job` invariant). The ciphertext
  artifact is safe to upload because anyone who downloads it cannot
  decrypt without a recipient private key.
- **Runners that elaborate untrusted Lean can be compromised.** The
  archiver App's installation token must never appear in the env of
  any job that runs untrusted Lean. It is therefore minted only in
  the `archive` job, which runs on a separate runner that has never
  touched the submitted source.
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
  verify that someone holds each recipient's private key; that
  requires a manual decrypt drill.
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

Conduct a decrypt drill periodically (annually is plenty) so that
recipient private keys are known to still exist on a reachable
device.

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
