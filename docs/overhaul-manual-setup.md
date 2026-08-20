# Remaining manual setup

Everything below requires an account UI or secret value. The Workers are
already deployed with intake disabled; do not repeat the bootstrap deployment.
Enter secrets only through the named interactive prompt. Never paste a value
into an issue, pull request, chat, shell argument, or tracked file.

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
Headless private-gist proof remains disabled because installation tokens cannot
read a submitter-owned private gist; this does not block browser OAuth intake.

## After these four tasks

Keep `INTAKE_ENABLED=false`. Merge the reviewed submissions PR, confirm the
automatic staging and production deployments, exercise browser intake in
staging, and finish the archive-locator callback before enabling production
intake. Results migration D7 is independent: run a fresh post-merge dry report
and obtain explicit approval of its exact source commit, record count, and
output digest before `apply=true`.
