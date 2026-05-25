# Audit archive recipient list

This directory holds the recipient list for the long-term audit
archive maintained at
[`leanprover/lean-eval-audit`](https://github.com/leanprover/lean-eval-audit).

`recipients.txt` is read by `age` in the `Submission` workflow to
encrypt every submission's source tarball before it is uploaded to the
audit repo. Each non-comment line is one recipient: a native age key
(`age1...`) or an SSH public key.

Anyone with the matching private key of any recipient line can
decrypt every archived submission, past or future. Adding a recipient
is therefore equivalent to granting permanent read access to the full
archive. Treat additions as PRs requiring maintainer review.

A push-time CI workflow (`.github/workflows/validate-recipients.yml`)
parses every line on every change so syntactically broken recipients
cannot reach `main`.

See [`docs/audit-archive.md`](../docs/audit-archive.md) for the full
design.
