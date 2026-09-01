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
      result_amendment_owner_api_enabled: false,
      result_amendment_maintainer_api_enabled: false,
      model_identity_owner_api_enabled: false,
      model_identity_maintainer_api_enabled: false,
      model_identity_consolidation_api_enabled: false,
      model_identity_write_max_subrequests: 400,
      model_identity_consolidation_api: "atomic_reverse_impact_v1",
      release_opt_in_api_enabled: false,
      release_opt_out_api_enabled: false,
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
      result_amendment_owner_api_enabled: false,
      result_amendment_maintainer_api_enabled: false,
      model_identity_owner_api_enabled: false,
      model_identity_maintainer_api_enabled: false,
      model_identity_consolidation_api_enabled: false,
      model_identity_write_max_subrequests: 400,
      model_identity_consolidation_api: "atomic_reverse_impact_v1",
      release_opt_in_api_enabled: false,
      release_opt_out_api_enabled: false,
      promotion_canary_configured_enabled: false,
      promotion_canary_enabled: false,
      intake_enablement_mode: "disabled",
      intake_lease_expires_at: null,
    });
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("reports opt-in independently and keeps the reverse transition disabled", async () => {
    const contract = "6105a6255ec40409bcce66c6cf6b6764e0e93ed4";
    const enabled = await handleRequest(
      new Request("https://example.test/healthz"),
      {
        ...ENV,
        MODEL_IDENTITY_OWNER_API_ENABLED: "true",
        MODEL_IDENTITY_CONSOLIDATION_API_ENABLED: "true",
        MODEL_IDENTITY_STATE_CONTRACT_COMMIT: contract,
        RELEASE_OPT_IN_API_ENABLED: "true",
        RELEASE_OPT_OUT_API_ENABLED: "true",
      },
      LIFECYCLE,
    );
    expect(await enabled.json()).toMatchObject({
      intake_effective_enabled: false,
      model_identity_owner_api_enabled: true,
      model_identity_consolidation_api_enabled: true,
      release_opt_in_api_enabled: true,
      release_opt_out_api_enabled: false,
    });

    const missingOwner = await handleRequest(
      new Request("https://example.test/healthz"),
      {
        ...ENV,
        MODEL_IDENTITY_CONSOLIDATION_API_ENABLED: "true",
        MODEL_IDENTITY_STATE_CONTRACT_COMMIT: contract,
      },
      LIFECYCLE,
    );
    expect(await missingOwner.json()).toMatchObject({
      model_identity_owner_api_enabled: false,
      model_identity_consolidation_api_enabled: false,
    });
  });

  it("routes later publication scheduling to its dedicated gated page", async () => {
    const enabledEnv: RuntimeEnv = { ...ENV, RELEASE_OPT_IN_API_ENABLED: "true" };
    const intake = await handleRequest(
      new Request("https://example.test/"),
      enabledEnv,
      LIFECYCLE,
    );
    const intakeBody = await intake.text();
    expect(intakeBody).toContain('href="/release/"');
    expect(intakeBody).not.toContain('id="release-opt-in-form"');

    const release = await handleRequest(
      new Request("https://example.test/release/"),
      enabledEnv,
      LIFECYCLE,
    );
    expect(release.status).toBe(200);
    expect(await release.text()).toContain('id="release-opt-in-form"');

    const disabled = await handleRequest(
      new Request("https://example.test/release/"),
      ENV,
      LIFECYCLE,
    );
    const disabledBody = await disabled.text();
    expect(disabledBody).toContain("Source release scheduling is currently unavailable");
    expect(disabledBody).not.toContain('id="release-opt-in-form"');
  });

  it("fails the maintainer gate closed and never exposes configured identities", async () => {
    const configured = JSON.stringify([{ github_id: 477956, login: "kim-em" }]);
    const enabled = await handleRequest(
      new Request("https://example.test/healthz"),
      {
        ...ENV,
        RESULT_AMENDMENT_MAINTAINER_API_ENABLED: "true",
        RESULT_AMENDMENT_MAINTAINERS: configured,
        RESULT_OWNER_STATE_CONTRACT_COMMIT:
          "6105a6255ec40409bcce66c6cf6b6764e0e93ed4",
      },
      LIFECYCLE,
    );
    const enabledBody = await enabled.json<Record<string, unknown>>();
    expect(enabledBody.result_amendment_maintainer_api_enabled).toBe(true);
    expect(JSON.stringify(enabledBody)).not.toContain("kim-em");
    expect(JSON.stringify(enabledBody)).not.toContain("477956");

    for (const env of [
      {
        ...ENV,
        RESULT_AMENDMENT_MAINTAINER_API_ENABLED: "true",
        RESULT_AMENDMENT_MAINTAINERS: "[]",
        RESULT_OWNER_STATE_CONTRACT_COMMIT:
          "6105a6255ec40409bcce66c6cf6b6764e0e93ed4",
      },
      {
        ...ENV,
        RESULT_AMENDMENT_MAINTAINER_API_ENABLED: "true",
        RESULT_AMENDMENT_MAINTAINERS: "not-json",
        RESULT_OWNER_STATE_CONTRACT_COMMIT:
          "6105a6255ec40409bcce66c6cf6b6764e0e93ed4",
      },
      {
        ...ENV,
        RESULT_AMENDMENT_MAINTAINER_API_ENABLED: "true",
        RESULT_AMENDMENT_MAINTAINERS: configured,
        RESULT_OWNER_STATE_CONTRACT_COMMIT: "b".repeat(40),
      },
    ] satisfies RuntimeEnv[]) {
      const response = await handleRequest(
        new Request("https://example.test/healthz"),
        env,
        LIFECYCLE,
      );
      expect(await response.json()).toMatchObject({
        result_amendment_maintainer_api_enabled: false,
      });
    }
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
    const contract = "235a96c96462438c7680e6fb90fa0e6044ec1774";
    const tree = "2".repeat(40);
    const rootEntries = [
      { path: "README.md", mode: "100644", type: "blob", sha: "1dd08b8569c1a3a8eadec72af96276f520d4afec" },
      { path: "docs", mode: "040000", type: "tree", sha: "7401f6bf26083ebbc0db05f11cd90007d2a74f80" },
      { path: "schema", mode: "040000", type: "tree", sha: "4cfe7363c7d8ab2d8ebf0cb2c4e26697c27ab680" },
      { path: "scripts", mode: "040000", type: "tree", sha: "402b090e3b9d3bf233fdc410ab684f108999d725" },
    ] as const;
    const replies = [
      Response.json({ permissions: { push: true } }),
      Response.json({ object: { sha: contract } }),
      Response.json({ tree: { sha: tree } }),
      Response.json({ name: "main", protected: true, commit: { sha: contract } }),
      Response.json({ sha: tree, truncated: false, tree: rootEntries }),
      Response.json({ ref: "refs/heads/main", object: { sha: contract } }),
      Response.json({ name: "main", protected: true, commit: { sha: contract } }),
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
        "5394e7f24901db390b16c97ac5ab781a407da6cedee60f96e3e9396bce549587",
    });
    expect(upstream).toHaveBeenCalledTimes(7);
    upstream.mockRestore();
  });

  it("returns JSON 404s", async () => {
    const response = await handleRequest(new Request("https://example.test/nope"), ENV, LIFECYCLE);
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: "not_found" });
  });
});
