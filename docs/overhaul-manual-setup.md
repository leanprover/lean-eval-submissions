# Manual setup record and remaining actions

The four provisioning procedures below were completed on 2026-08-21, and the
dedicated AWS key-custody account and stacks were provisioned on 2026-08-22.
The two State-writer tokens are approved and preflighted, and both broker GitHub Apps
are owned by `leanprover` with unchanged IDs. The source reader is installed
only on the private staging fixture and passed its live broker preflight. The
separate evaluation App is also installed on that fixture; exact private
archival and evaluation fetches pass. Both intake Workers are currently
disabled. Never
paste a secret value into an issue, pull request, chat, shell argument, or
tracked file.

## 1. Cloudflare deployment tokens

In the dedicated Cloudflare account `lean-eval`
(`a46b90978a1c29cc4795f30677e7e4b8`), create two account-owned API tokens:

- `lean-eval-deploy-staging`
- `lean-eval-deploy-production`

Each token originally received only **Workers Scripts: Edit** on that one
account. The approved Cloudflare Sandbox replay executor additionally requires
**Containers: Edit** on the same account so Wrangler can publish and roll out
its image. Do not add zone, DNS, KV, R2, billing, Access, or
account-administration permissions. Cloudflare does not offer per-script
resource scoping for these permissions; the dedicated account is the isolation
boundary and the separate tokens provide independent revocation and rotation.

To expand the existing account-owned tokens without creating another
credential:

1. In the Cloudflare dashboard select the `lean-eval` account, then open
   **Manage Account > API Tokens**.
2. Edit `lean-eval-deploy-staging`.
3. Keep **Account > Workers Scripts > Edit** and add exactly
   **Account > Containers > Edit**.
4. Keep the resource restricted to the one `lean-eval` account. Leave zone
   resources and every other permission absent. Retain the recorded no-expiry
   and no-IP-filter policy.
5. Review the summary and save the edit. Editing an existing token does not
   require changing the matching GitHub secret unless Cloudflare explicitly
   rotates or replaces its value.
6. Repeat steps 2--5 for `lean-eval-deploy-production`.

If the dashboard requires replacement rather than editing, create a replacement
with exactly the two permissions and one-account scope above, update only the
matching GitHub environment secret using the commands below, verify one
deployment, and then revoke the old token. Never have one environment use the
other environment's token.

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

1. The Cloudflare Sandbox replay backend and automatic deployment are
   implemented. Workers Paid is active and both deployment tokens were
   operator-confirmed with Containers: Edit on 2026-08-22. The first protected
   deployment and source-free staging acceptance must still verify image
   publication, rollout, OIDC, network denial, refusal, and destruction. The
   controller does not pass AWS, GitHub, or Cloudflare credentials into the
   untrusted sandbox.
2. Keep all production AWS role variables unset until the corresponding
   archive, replay, and release workflows and their launch gates are reviewed.

When the production launch gates are later satisfied, use
[`intake-transition-announcements.md`](intake-transition-announcements.md) for
the dated server-launch notice and the separate evidence-backed issue-intake
closure notice. The template does not authorize either action.

The archive callback uses a distinct random `LIFECYCLE_CALLBACK_TOKEN` in each
Worker and the matching `cloudflare-staging` or `cloudflare-production` GitHub
environment. Both pairs were installed on 2026-08-21. Never copy this value
into repository-level secrets: only the source-free `archive_state` job should
receive it.
