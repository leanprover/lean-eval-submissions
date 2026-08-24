import { exports } from "cloudflare:workers";
import { createExecutionContext, waitOnExecutionContext } from "cloudflare:test";
import { describe, expect, it, vi } from "vitest";

import { handleRequest, type RuntimeEnv } from "../src/app";

const ENV = {
  API_RATE_LIMITER: { limit: () => Promise.resolve({ success: true }) },
  DEPLOYED_COMMIT: "test-commit",
  DEPLOYMENT_ENVIRONMENT: "staging",
  INTAKE_ENABLED: "false",
  INTAKE_ENABLEMENT_MODE: "disabled",
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
      intake_configured_enabled: false,
      intake_effective_enabled: false,
      intake_enabled: false,
      legacy_result_owner_api_enabled: false,
      promotion_canary_configured_enabled: true,
      promotion_canary_enabled: true,
      intake_enablement_mode: "disabled",
      intake_lease_expires_at: null,
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
      intake_configured_enabled: false,
      intake_effective_enabled: false,
      intake_enabled: false,
      legacy_result_owner_api_enabled: false,
      promotion_canary_configured_enabled: false,
      promotion_canary_enabled: false,
      intake_enablement_mode: "disabled",
      intake_lease_expires_at: null,
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
      INTAKE_ENABLEMENT_MODE: "durable",
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

  it("proves State write authority while intake remains disabled", async () => {
    const replies = [
      Response.json({ permissions: { push: true } }),
      Response.json({ object: { sha: "1".repeat(40) } }),
      Response.json({ tree: { sha: "2".repeat(40) } }),
      Response.json({ ref: "refs/heads/main", object: { sha: "1".repeat(40) } }),
    ];
    const upstream = vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      const reply = replies.shift();
      if (!reply) return Promise.reject(new Error("unexpected GitHub request"));
      return Promise.resolve(reply);
    });
    const response = await handleRequest(
      new Request("https://example.test/readyz", {
        method: "POST",
        headers: { authorization: "Bearer readiness-secret" },
      }),
      {
        ...ENV,
        GITHUB_STATE_TOKEN: "state-secret",
        READINESS_TOKEN: "readiness-secret",
      },
      LIFECYCLE,
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      status: "state_writer_ready",
      environment: "staging",
      intake_configured_enabled: false,
      intake_effective_enabled: false,
      intake_enabled: false,
      intake_enablement_mode: "disabled",
      intake_lease_expires_at: null,
      state_commit: "1".repeat(40),
    });
    expect(upstream).toHaveBeenCalledTimes(4);
    const [, init] = upstream.mock.calls[3] ?? [];
    expect(init?.method).toBe("PATCH");
    upstream.mockRestore();
  });

  it("returns a closed protected-contract proof for production readiness", async () => {
    const contract = "0c8759946df0da1338a0c73bf5bd75d182038286";
    const blobs = [
      ["docs/result-owner-operational-indexes.md", "2f784609f9117caf74cb7042e9ea45732925d77b"],
      ["schema/result-identity-guard-v1.schema.json", "1620b6d8aed37f652958ac86e311c00578edc8b4"],
      ["schema/result-overlay-view-v1.schema.json", "1b50a92a76891bd21e0b67f7f40ab9c86d50beed"],
      ["schema/result-overlays-v1.schema.json", "41d4078133d6854bf8de839873a3f58e9ba1afd1"],
      ["schema/result-source-record-index-v1.schema.json", "4543225e0833af00913e436185532a769debebc1"],
      ["schema/state-event-v1.schema.json", "fcb267369516ce4ff5344ca75529c1d280970b0a"],
      ["scripts/materialize_state.py", "24c0569ef69c4f7e24283d8d39b88f2055a33b77"],
      ["scripts/result_owner_indexes.py", "c07c29a81eb2ca5058563a8411c26f9358bde3e4"],
      ["scripts/validate_state.py", "b23380497da0b3b85d555b92af9eb350441e1977"],
    ] as const;
    const replies = [
      Response.json({ permissions: { push: true } }),
      Response.json({ object: { sha: contract } }),
      Response.json({ tree: { sha: "2".repeat(40) } }),
      Response.json({ name: "main", protected: true, commit: { sha: contract } }),
      ...blobs.map(([path, sha]) => Response.json({ type: "file", path, sha })),
      Response.json({ ref: "refs/heads/main", object: { sha: contract } }),
    ];
    const upstream = vi.spyOn(globalThis, "fetch").mockImplementation(() => {
      const reply = replies.shift();
      if (!reply) return Promise.reject(new Error("unexpected GitHub request"));
      return Promise.resolve(reply);
    });
    const response = await handleRequest(
      new Request("https://example.test/readyz", {
        method: "POST",
        headers: { authorization: "Bearer readiness-secret" },
      }),
      {
        ...ENV,
        DEPLOYMENT_ENVIRONMENT: "production",
        STATE_REPOSITORY: "leanprover/lean-eval-state",
        GITHUB_STATE_TOKEN: "state-secret",
        READINESS_TOKEN: "readiness-secret",
      },
      LIFECYCLE,
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      status: "state_writer_ready",
      environment: "production",
      intake_configured_enabled: false,
      intake_effective_enabled: false,
      intake_enabled: false,
      intake_enablement_mode: "disabled",
      intake_lease_expires_at: null,
      state_branch_protected: true,
      state_commit: contract,
      state_contract_commit: contract,
      state_contract_verified: true,
      state_event_schema_sha256:
        "bfacfb44083c60372cef6b82637ff523a9454d49dc3e731fe97056f7402a6e4a",
    });
    expect(upstream).toHaveBeenCalledTimes(14);
    upstream.mockRestore();
  });

  it("returns JSON 404s", async () => {
    const response = await handleRequest(new Request("https://example.test/nope"), ENV, LIFECYCLE);
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: "not_found" });
  });
});
