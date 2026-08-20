import { describe, expect, it } from "vitest";

import { handleRequest, type RuntimeEnv } from "../src/app";

const ENV = {
  DEPLOYMENT_ENVIRONMENT: "staging",
  INTAKE_ENABLED: "false",
  STATE_REPOSITORY: "leanprover/lean-eval-state-staging",
} satisfies RuntimeEnv;

describe("Worker routing", () => {
  it("serves a secret-free health response", async () => {
    const response = await handleRequest(new Request("https://example.test/healthz"), ENV);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      status: "ok",
      service: "lean-eval-submission",
      environment: "staging",
      intake_enabled: false,
    });
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("fails closed while intake is disabled", async () => {
    const ready = await handleRequest(new Request("https://example.test/readyz"), ENV);
    expect(ready.status).toBe(503);
    expect(await ready.json()).toMatchObject({ reason: "intake_disabled" });

    const intake = await handleRequest(
      new Request("https://example.test/api/submissions", { method: "POST" }),
      ENV,
    );
    expect(intake.status).toBe(503);
  });

  it("returns JSON 404s", async () => {
    const response = await handleRequest(new Request("https://example.test/nope"), ENV);
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: "not_found" });
  });
});
