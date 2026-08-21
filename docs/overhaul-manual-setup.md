# Manual setup record and remaining actions

The four provisioning procedures below were completed on 2026-08-21. The two
State-writer tokens are approved and preflighted, and both broker GitHub Apps
are owned by `leanprover` with unchanged IDs. The source reader is installed
only on the private staging fixture and passed its live broker preflight.
Staging intake is enabled only for that end-to-end fixture; production intake
remains disabled. Never
paste a secret value into an issue, pull request, chat, shell argument, or
tracked file.

## 1. Cloudflare deployment tokens

In the dedicated Cloudflare account `lean-eval`
(`a46b90978a1c29cc4795f30677e7e4b8`), create two account-owned API tokens:

- `lean-eval-deploy-staging`
- `lean-eval-deploy-production`

Each token gets only **Workers Scripts: Edit** on that one account. Do not add
zone, DNS, KV, R2, billing, or account-administration permissions. Cloudflare
does not offer per-script resource scoping for this permission; the dedicated
account is the isolation boundary and the separate tokens provide independent
revocation and rotation.

Install each token in its matching GitHub environment:

```bash
gh secret set CLOUDFLARE_API_TOKEN \
  --repo leanprover/lean-eval-submissions --env cloudflare-staging
gh secret set CLOUDFLARE_API_TOKEN \
  --repo leanprover/lean-eval-submissions --env cloudflare-production
```

`CLOUDFLARE_ACCOUNT_ID` is already installed in both environments.

## 2. State-writer tokens

Create two GitHub fine-grained personal access tokens, each expiring in at
most 90 days:

| Environment | Only selected repository | Permissions |
| --- | --- | --- |
| staging | private `leanprover/lean-eval-state-staging` | Metadata read; Contents read/write |
| production | private `leanprover/lean-eval-state` | Metadata read; Contents read/write |

Do not grant Actions, Administration, Workflows, Issues, or organization-wide
repository access. Install them interactively from `server/`:

```bash
npx wrangler secret put GITHUB_STATE_TOKEN --env staging
npx wrangler secret put GITHUB_STATE_TOKEN --env production
```

Record each token's owner, creation date, and expiry date in
`INFRASTRUCTURE.md`, but never its value. If the repository ruleset rejects an
append, add only the token's principal to the matching State ruleset bypass;
never give one environment authority over the other State repository.

After organization approval, run `verify-state-writer.yml` once for staging
and once for production from protected `main`. The workflow uses the matching
environment's `READINESS_TOKEN`; it never receives the State credential. The
Worker proves the stored credential's read/write authority and ruleset bypass
with a non-forced update of the State branch to its existing commit, so the
repository graph and tree remain unchanged and intake stays disabled.

## 3. Browser OAuth Apps

Create two GitHub OAuth Apps, one for each environment. They need only the
`read:user` scope. Use these exact callback URLs:

- staging: `https://lean-eval-submission-server-staging.lean-eval.workers.dev/api/v1/oauth/callback`
- production: `https://lean-eval-submission-server.lean-eval.workers.dev/api/v1/oauth/callback`

Install each client ID and secret in the matching Worker:

```bash
npx wrangler secret put GITHUB_OAUTH_CLIENT_ID --env staging
npx wrangler secret put GITHUB_OAUTH_CLIENT_SECRET --env staging
npx wrangler secret put GITHUB_OAUTH_CLIENT_ID --env production
npx wrangler secret put GITHUB_OAUTH_CLIENT_SECRET --env production
```

Record the non-secret App names and client IDs in `INFRASTRUCTURE.md`.

## 4. GitHub Apps for the private broker

Create two separate GitHub Apps:

| App | Repository permissions | Installation |
| --- | --- | --- |
| source reader | Metadata read; Contents read | contributor source repositories that opt in |
| workflow dispatcher | Metadata read; Contents read; Actions write | only `leanprover/lean-eval-submissions` |

Do not give the source reader Actions write, and do not install the dispatcher
on contributor repositories. Generate one private key for each App, then
install the four matching secrets in both broker environments:

```bash
npx wrangler secret put SOURCE_APP_ID --config wrangler.broker.jsonc --env staging
npx wrangler secret put SOURCE_APP_PRIVATE_KEY --config wrangler.broker.jsonc --env staging
npx wrangler secret put DISPATCH_APP_ID --config wrangler.broker.jsonc --env staging
npx wrangler secret put DISPATCH_APP_PRIVATE_KEY --config wrangler.broker.jsonc --env staging

npx wrangler secret put SOURCE_APP_ID --config wrangler.broker.jsonc --env production
npx wrangler secret put SOURCE_APP_PRIVATE_KEY --config wrangler.broker.jsonc --env production
npx wrangler secret put DISPATCH_APP_ID --config wrangler.broker.jsonc --env production
npx wrangler secret put DISPATCH_APP_PRIVATE_KEY --config wrangler.broker.jsonc --env production
```

Record App and installation IDs, not private keys, in `INFRASTRUCTURE.md`.
Headless proof uses a GitHub secret gist as an unlisted challenge location.
GitHub documents secret gists as readable by anyone who knows the URL, so the
Worker fetches the exact gist ID anonymously and verifies its owner,
`public: false`, untruncated proof file, and signed expiring content. Do not add
gist permission to either App or browser OAuth.

Ownership transfers for both existing registrations were accepted by
`leanprover` on 2026-08-21. Public App records confirm unchanged IDs `4666604`
and `4666633`; dispatcher installation `155329316` remains the recorded
single-repository installation on `leanprover/lean-eval-submissions`.

## Current remaining manual actions

Keep production `INTAKE_ENABLED=false`.

1. In <https://github.com/settings/installations>, configure the existing
   `lean-eval-bot` installation owned by `kim-em`, add only
   `kim-em/lean-eval-intake-fixture`, and save. This is the read-only App used
   by the existing evaluation workflow after dispatch; the new source-reader
   App already covers intake verification. Retry workflow run `32478988233`
   rather than creating a duplicate submission.
2. Review the live leaderboard at <https://lean-lang.org/eval/preview/> and
   approve or request changes to `lean-eval-leaderboard#70` before cutover.
3. Leave D7 unapplied until explicitly approving a current dry report's exact
   source commit, record count, and output digest.
4. Create the dedicated Lean Eval AWS account. In `us-east-1`, add GitHub's OIDC
   provider (`https://token.actions.githubusercontent.com`, audience
   `sts.amazonaws.com`), then deploy
   `infrastructure/aws-key-adapter/template.yaml` as
   `lean-eval-key-adapter-staging` and
   `lean-eval-key-adapter-production` with the corresponding environment
   parameter and that provider ARN. Record the stack outputs in
   `INFRASTRUCTURE.md`; never create an IAM access key. The template is ready,
   but no AWS resource exists yet. Exact commands and output fields are in
   [`aws-key-adapter-setup.md`](aws-key-adapter-setup.md).
5. A reviewed disposable replay backend is still required before private
   replay, automatic release, or production intake. It must call the Lambda
   through the controller's Invoke-only role and must not pass AWS credentials
   into the untrusted VM.

The archive callback uses a distinct random `LIFECYCLE_CALLBACK_TOKEN` in each
Worker and the matching `cloudflare-staging` or `cloudflare-production` GitHub
environment. Both pairs were installed on 2026-08-21. Never copy this value
into repository-level secrets: only the source-free `archive_state` job should
receive it.
