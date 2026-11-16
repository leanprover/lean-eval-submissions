# Isolate State and leaderboard publication from personal API quotas

Production State writes and leaderboard dispatches previously depended on
personal access tokens. All tokens issued by the same GitHub user share that
user's REST quota, so unrelated API activity could disable browser sign-in,
measurement publication, and leaderboard updates together.

This migration uses two private GitHub Apps and repository-scoped installation
tokens. It must not be merged until both Apps are installed and their secrets
are provisioned. App IDs and key fingerprints must be recorded here when
provisioning is complete; no secret values belong in this repository.

## Provisioning

1. Register **Lean Eval State Writer**, privately under `leanprover`, with
   Contents read/write, Metadata read, and webhooks disabled. Install it only
   on `lean-eval-state` and `lean-eval-state-staging`.
2. Register **Lean Eval Leaderboard Publisher** with the same permissions and
   webhooks disabled. Install it only on `lean-eval-leaderboard`.
3. Generate a separate PEM private key for each App. Install repository secrets
   in `leanprover/lean-eval-submissions`:
   `LEAN_EVAL_STATE_APP_ID`, `LEAN_EVAL_STATE_APP_PRIVATE_KEY`,
   `LEAN_EVAL_LEADERBOARD_APP_ID`, `LEAN_EVAL_LEADERBOARD_APP_PRIVATE_KEY`.
   Use `gh secret set NAME -R leanprover/lean-eval-submissions` with the value
   on standard input; never put a private key in command arguments or logs.
4. Add an `Integration` bypass actor with the State Writer's numeric App ID
   and `bypass_mode: always` to State rulesets `21094006` (staging) and
   `21094005` (production). Preserve the existing actors, conditions, and
   rules. Save the prior rule definitions for rollback. Do not grant this
   App administration or workflow permissions.
5. Mint a token with the State App, restricting it to each repository in turn.
   Verify repository identity and Contents write permission, then PATCH
   `git/refs/heads/main` to its existing SHA with `force: false`. This proves
   the bypass without adding or modifying ledger data.
6. Mint a Leaderboard Publisher token restricted to its one repository and
   dispatch `results-advanced`. Confirm the resulting Pages run succeeds.

## Deployment and verification

The protected deployment workflow installs State App credentials in the
private brokers before deploying intake. Staging smoke tests and State
readiness gates precede production finalization. Keys never enter the public
Worker or untrusted evaluation jobs. The State adapter has no PAT fallback.

After deployment:

```sh
gh workflow run verify-state-writer.yml \
  -R leanprover/lean-eval-submissions -f target_environment=production
gh workflow run lifecycle-readiness-monitor.yml \
  -R leanprover/lean-eval-submissions
```

Verify a complete browser OAuth sign-in, which must append the consumed nonce,
and one accepted submission's measurement publication and leaderboard refresh.
Confirm both operations use installation-token rate headers rather than the
maintainer's personal quota. The scheduled reconciler retries only failed
publication jobs; acceptance, archival, and proof evaluation are not repeated.

The bootstrap `GITHUB_STATE_TOKEN` runtime secrets remain declared solely for
rollback to the previous code during the migration window. Current code never
reads them. Retire their declarations and values together after verification;
otherwise the exact Cloudflare capability check will detect a mismatch.
The submissions workflow no longer reads `LEADERBOARD_WRITE_TOKEN`; copies
used by other repositories are outside this migration and must remain valid
until those callers migrate.

## Recovery of the reported submission

Submission issue `leanprover/lean-eval-submissions#2186` accepted
`two_ninety_theorem` in run `43559042433`. After personal quota capacity
returned on 2026-11-16, production State verification run `43777903005` passed,
and the retried `measurement_state` job passed in attempt 2 of the submission
run. The dispatched leaderboard build `43777910961` exposed a separate receipt
compatibility error: original verification receipts now have an optional
`toolchain` field. Leaderboard PR `leanprover/lean-eval-leaderboard#108` fixes
that schema mismatch without changing accepted credit.
