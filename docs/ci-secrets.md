# CI secrets and access control for `leanprover/lean-eval-submissions`

This document is the source of truth for every CI credential and branch-
protection setting the submission pipeline depends on. It is written so
that, on a fresh clone or a brand-new repo, you can reconstruct the
entire CI auth posture from this file alone — no UI screenshots, no
tribal knowledge.

The benchmark repository `leanprover/lean-eval` has its own
`docs/ci-secrets.md` covering its `lean-eval-regenerator` App; that one
is unrelated to this pipeline.

## Audit checklist

| Item | Type | Stored as | Used by |
| --- | --- | --- | --- |
| `lean-eval-bot` | GitHub App | `LEAN_EVAL_BOT_APP_ID`, `LEAN_EVAL_BOT_PRIVATE_KEY` | `submission.yml` (fetch) |
| `lean-eval-recorder` | GitHub App | `LEAN_EVAL_RECORDER_APP_ID`, `LEAN_EVAL_RECORDER_PRIVATE_KEY` | `submission.yml` (record) |
| `LEADERBOARD_WRITE_TOKEN` | Fine-grained PAT | `LEADERBOARD_WRITE_TOKEN` | `submission.yml` (leaderboard redeploy dispatch) |
| Ruleset `main protection` | Repository Ruleset | (config in this file, applied via API) | branch protection on `main` |

To check the live state at any time:

```bash
gh secret list -R leanprover/lean-eval-submissions
gh api /repos/leanprover/lean-eval-submissions/rules/branches/main --jq '[.[].type]'
```

## GitHub App: `lean-eval-bot`

Used by [`.github/workflows/submission.yml`](../.github/workflows/submission.yml)
to mint installation tokens that fetch submission source from contributor
repositories (which may be private).

### App settings

- Owner account: `kim-em` (User account).
- Webhook: deactivated.
- Repository permissions:
  - `Contents: Read`
- Where can this GitHub App be installed: **Any account**. Contributors
  install it on their own submission repos so the workflow can clone them.

### Repository secrets (in `leanprover/lean-eval-submissions`)

- `LEAN_EVAL_BOT_APP_ID` — the App ID number.
- `LEAN_EVAL_BOT_PRIVATE_KEY` — the full PEM contents of a private key
  generated for the app.

### Where used

[`.github/workflows/submission.yml`](../.github/workflows/submission.yml),
in the `Mint lean-eval-bot installation token` step, via
`actions/create-github-app-token`. The minted token is scoped to the
single `Fetch submission` step and is used to clone the contributor's
submission source.

The issue template
[`.github/ISSUE_TEMPLATE/submit.yml`](../.github/ISSUE_TEMPLATE/submit.yml)
instructs contributors to install this app on their submission repo.

### Reconstruction from scratch

This is the same App that previously served `leanprover/lean-eval`'s
submission workflow. The migration step is to install it on
`leanprover/lean-eval-submissions` and copy its secrets here. A
from-scratch rebuild:

1. As the desired app owner, visit <https://github.com/settings/apps/new>.
2. Fill in:
   - Name: `lean-eval-bot`
   - Homepage URL: `https://github.com/leanprover/lean-eval-submissions`
   - Webhook → Active: unchecked
   - Repository permissions → Contents: Read
   - Where can this GitHub App be installed: **Any account**
3. Save → record the App ID.
4. Generate a private key, download the `.pem`.
5. Install the app on `leanprover/lean-eval-submissions` (so the workflow
   has an installation to mint tokens against).
6. Set the secrets:
   ```bash
   gh secret set LEAN_EVAL_BOT_APP_ID -R leanprover/lean-eval-submissions --body <APP_ID>
   gh secret set LEAN_EVAL_BOT_PRIVATE_KEY -R leanprover/lean-eval-submissions < path/to/key.pem
   ```

## GitHub App: `lean-eval-recorder`

Used by [`.github/workflows/submission.yml`](../.github/workflows/submission.yml)
to push the `record:` commit (a results-store update) directly to this
repo's `main`, bypassing branch protection.

### App settings

- Owner account: `kim-em` (User account).
- Webhook: deactivated.
- Repository permissions:
  - `Contents: Read and write`
- Where can this GitHub App be installed: **Any account**. (Required so
  the org can install it; the only intended installation is on
  `leanprover/lean-eval-submissions` itself.)
- Installed on: `leanprover/lean-eval-submissions` only (single-repo
  installation).

This is a **distinct App from `lean-eval-bot`**, on purpose:
`lean-eval-bot` is installed on arbitrary contributor repositories, so it
must stay `Contents: Read` only. A write-capable App must never be
installable on third-party repos.

### Repository secrets (in `leanprover/lean-eval-submissions`)

- `LEAN_EVAL_RECORDER_APP_ID` — the App ID number.
- `LEAN_EVAL_RECORDER_PRIVATE_KEY` — the full PEM contents of a private
  key generated for the app.

### Where used

[`.github/workflows/submission.yml`](../.github/workflows/submission.yml),
in the `Mint lean-eval-recorder installation token` step. The token
authenticates the `results-store/` checkout so the push-retry loop's
`git push origin HEAD:main` lands on protected `main`.

### Why an app and not `GITHUB_TOKEN`

`GITHUB_TOKEN`-authored pushes cannot bypass branch protection (no actor
can put `github-actions[bot]` itself in the bypass list at workflow
granularity). A dedicated app's principal can be in the bypass list (see
the Ruleset section below); the bypass is then narrow because only this
workflow has the app's secrets.

### Reconstruction from scratch

1. As the desired app owner, visit <https://github.com/settings/apps/new>.
2. Fill in:
   - Name: `lean-eval-recorder`
   - Homepage URL: `https://github.com/leanprover/lean-eval-submissions`
   - Webhook → Active: unchecked
   - Repository permissions → Contents: Read and write (everything else
     stays "No access")
   - Where can this GitHub App be installed: **Any account**
3. Save → record the App ID.
4. Generate a private key, download the `.pem`.
5. Install the app on `leanprover/lean-eval-submissions` only.
6. Set the secrets:
   ```bash
   gh secret set LEAN_EVAL_RECORDER_APP_ID -R leanprover/lean-eval-submissions --body <APP_ID>
   gh secret set LEAN_EVAL_RECORDER_PRIVATE_KEY -R leanprover/lean-eval-submissions < path/to/key.pem
   ```
7. Add the App ID to the `main` ruleset's bypass list (see Ruleset
   section below).

## PAT: `LEADERBOARD_WRITE_TOKEN`

Fine-grained Personal Access Token used by
[`.github/workflows/submission.yml`](../.github/workflows/submission.yml)
to fire a `repository_dispatch` (`event_type: results-advanced`) at
<https://github.com/leanprover/lean-eval-leaderboard> after a result is
recorded, so the leaderboard site redeploys with the new result.

This is the same PAT `leanprover/lean-eval` and `leanprover/lean-eval-leaderboard`
already use for their leaderboard interactions; the submission pipeline
just needs its own copy of the secret.

### Repository secrets

- `LEADERBOARD_WRITE_TOKEN` in `leanprover/lean-eval-submissions`.

The token must be a fine-grained PAT with
`leanprover/lean-eval-leaderboard` selected and `Contents: Read and
write` — that permission is what the `repository_dispatch` REST endpoint
authorizes on (per
<https://docs.github.com/en/rest/repos/repos#create-a-repository-dispatch-event>).

### Reconstruction from scratch

1. Open <https://github.com/settings/personal-access-tokens/new>.
2. Resource owner: **leanprover** (requires org-owner approval).
3. Repository access: **Only select repositories** →
   `leanprover/lean-eval-leaderboard`.
4. Repository permissions: **Contents: Read and write**.
5. Save the token, then write it to this repo:
   ```bash
   gh secret set LEADERBOARD_WRITE_TOKEN -R leanprover/lean-eval-submissions --body <TOKEN>
   ```

When rotating the PAT, update the copies in `leanprover/lean-eval` and
`leanprover/lean-eval-leaderboard` together (see those repos' own docs).

## Branch protection on `main` (Repository Ruleset)

`main` is protected by a Repository Ruleset. The `lean-eval-recorder` app
is on the bypass list so the `record` job can push results directly;
everyone and everything else goes through a PR with a passing `verify`
check.

### Live state inspection

```bash
gh api /repos/leanprover/lean-eval-submissions/rulesets \
    --jq '.[] | {id, name, target, enforcement}'
gh api "/repos/leanprover/lean-eval-submissions/rulesets/<ID>" \
    --jq '{name, bypass_actors}'
```

### Ruleset payload (canonical)

The `<RECORDER_APP_ID>` placeholder is the `lean-eval-recorder` App ID.
`integration_id: 15368` pins the `verify` status check to the GitHub
Actions app, so a hostile third-party app can't satisfy it with a bogus
check of the same name.

```json
{
  "name": "main protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false } },
    { "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "required_status_checks": [ { "context": "verify", "integration_id": 15368 } ] } }
  ],
  "bypass_actors": [
    { "actor_id": <RECORDER_APP_ID>, "actor_type": "Integration", "bypass_mode": "always" }
  ]
}
```

### Reconstruction from scratch

1. Make sure the `lean-eval-recorder` app exists, is installed on the
   repo, and you know its App ID.
2. Save the payload above to `/tmp/main-ruleset.json`, substituting the
   App ID.
3. Apply:
   ```bash
   gh api -X POST /repos/leanprover/lean-eval-submissions/rulesets \
       --input /tmp/main-ruleset.json
   ```

### Acceptable consequences of the bypass

- The `record` job's results push to `main` does not need to re-trigger
  any workflow: the leaderboard redeploy is driven by an explicit
  `results-advanced` `repository_dispatch`, and `ci.yml` running (or not)
  on a machine-authored results commit changes nothing — `results/*.json`
  is not exercised by `ci.yml`.
- Anyone who can land a PR that modifies `submission.yml` to push
  arbitrary content to `main` could, after merge, exfiltrate that
  capability. This is the same trust boundary as merging any PR.
