import { cloudflareTest } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

for (const name of [
  "AUTH_TOKEN_SECRET",
  "GITHUB_OAUTH_CLIENT_ID",
  "GITHUB_OAUTH_CLIENT_SECRET",
  "GITHUB_STATE_TOKEN",
  "LIFECYCLE_CALLBACK_TOKEN",
  "READINESS_TOKEN",
]) {
  process.env[name] ??= `test-only-${name.toLowerCase()}`;
}

export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: { configPath: "./wrangler.jsonc", environment: "staging" },
      miniflare: {
        serviceBindings: {
          GITHUB_BROKER: () => Promise.resolve(Response.json({ error: "broker_not_configured_in_main_worker_tests" }, { status: 503 })),
        },
      },
    }),
  ],
});
