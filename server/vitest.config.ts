import { cloudflareTest } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

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
