# lean-eval submission Worker

This directory contains the Cloudflare Worker that will replace GitHub Issues
as the submission intake boundary. It is intentionally deployed with
`INTAKE_ENABLED=false` until the state repositories, GitHub credentials, abuse
controls, and replay key design have passed their rollout gates.

The first implemented primitive is append-only Git state. Each durable event is
stored in its own `events/<id-prefix>/<event-id>.json` file. The ID-derived
partition makes retries independent of wall-clock date. A write reads one
branch head, creates a tree and commit, and advances `main` with a non-forced
reference update. A competing writer causes the whole decision to be retried
from the new head; an uncertain GitHub response is checked for reachability and
never reported as a definite failure when the outcome cannot be established.

## Local checks

```bash
npm ci
npm run check
```

`npm run types` regenerates `worker-configuration.d.ts` from `wrangler.jsonc`;
CI uses `npm run types:check` so a stale checked-in binding file fails instead
of being silently rewritten. The test suite runs inside Cloudflare's local
Workers runtime and includes an integration call through the deployed module
entrypoint; GitHub compare-and-swap branches remain covered with mocked API
responses.
Local secrets belong in the ignored `.dev.vars` file. Never add token values to
Wrangler configuration, tests, documentation, or workflow command lines.

## Deployment

Merges to protected `main` that touch this directory deploy staging, run a
smoke test, then deploy production and run the same smoke test. GitHub
Environments named `cloudflare-staging` and `cloudflare-production` hold their
own deployment credentials. See [`../INFRASTRUCTURE.md`](../INFRASTRUCTURE.md)
for the complete inventory, ownership, setup, and recovery record.
The authentication and source-boundary design is recorded in
[`../docs/intake-threat-model.md`](../docs/intake-threat-model.md); every launch
gate there remains mandatory while intake is disabled.
