import { exports } from "cloudflare:workers";
import { createExecutionContext, waitOnExecutionContext } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";

import { handleRequest, type RuntimeEnv } from "../src/app";

const ENV = {
  API_RATE_LIMITER: { limit: () => Promise.resolve({ success: true }) },
  DEPLOYED_COMMIT: "test-commit",
  DEPLOYMENT_ENVIRONMENT: "staging",
  INTAKE_ENABLED: "false",
  STATE_REPOSITORY: "leanprover/lean-eval-state-staging",
} satisfies RuntimeEnv;

const LIFECYCLE = { waitUntil: () => undefined };

describe("Worker routing", () => {
  it("runs the deployed entrypoint in the Workers runtime", async () => {
    const response = await exports.default.fetch("https://example.test/healthz");
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      deployed_commit: "development",
      environment: "staging",
      intake_enabled: false,
    });
  });

  it("serves a secret-free health response", async () => {
    const response = await handleRequest(
      new Request("https://example.test/healthz"),
      ENV,
      LIFECYCLE,
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      status: "ok",
      service: "lean-eval-submission",
      deployed_commit: "test-commit",
      environment: "staging",
      intake_enabled: false,
    });
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("fails closed while intake is disabled", async () => {
    const ready = await handleRequest(new Request("https://example.test/readyz"), ENV, LIFECYCLE);
    expect(ready.status).toBe(404);

    const authorizedReady = await handleRequest(
      new Request("https://example.test/readyz", {
        headers: { authorization: "Bearer readiness-secret" },
      }),
      { ...ENV, READINESS_TOKEN: "readiness-secret" },
      LIFECYCLE,
    );
    expect(authorizedReady.status).toBe(503);
    expect(await authorizedReady.json()).toMatchObject({ reason: "intake_disabled" });

    const intake = await handleRequest(
      new Request("https://example.test/api/submissions", { method: "POST" }),
      ENV,
      LIFECYCLE,
    );
    expect(intake.status).toBe(503);
  });

  it("authenticates, checks write readiness, and caches the result", async () => {
    const replies = [
      Response.json({ permissions: { push: true } }),
      Response.json({ object: { sha: "1".repeat(40) } }),
      Response.json({ tree: { sha: "2".repeat(40) } }),
    ];
    const upstream = vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      const reply = replies.shift();
      if (!reply) return Promise.reject(new Error("unexpected uncached GitHub request"));
      return Promise.resolve(reply);
    });
    const readyEnv = {
      ...ENV,
      GITHUB_STATE_TOKEN: "state-secret",
      INTAKE_ENABLED: "true",
      READINESS_TOKEN: "readiness-secret",
    } satisfies RuntimeEnv;
    const request = new Request("https://example.test/readyz", {
      headers: { authorization: "Bearer readiness-secret" },
    });
    const firstContext = createExecutionContext();
    const first = await handleRequest(request, readyEnv, firstContext);
    await waitOnExecutionContext(firstContext);
    expect(first.status).toBe(200);
    expect(await first.json()).toEqual({ status: "ready", environment: "staging" });
    expect(upstream).toHaveBeenCalledTimes(3);

    const secondContext = createExecutionContext();
    const second = await handleRequest(request, readyEnv, secondContext);
    await waitOnExecutionContext(secondContext);
    expect(second.status).toBe(200);
    expect(await second.json()).toEqual({ status: "ready", environment: "staging" });
    expect(upstream).toHaveBeenCalledTimes(3);
    upstream.mockRestore();
  });

  it("returns JSON 404s", async () => {
    const response = await handleRequest(new Request("https://example.test/nope"), ENV, LIFECYCLE);
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: "not_found" });
  });
});
