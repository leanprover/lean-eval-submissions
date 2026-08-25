import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import executor from "../src/model-identity-qualification-executor";
import {
  signQualificationExecutorCapability,
  verifyQualificationExecutorCapability,
  type QualificationExecutorCapability,
} from "../src/model-identity-qualification-capability";

const SECRET = "test-only-qualification-executor-capability-secret";
const NOW = 1_787_652_000;

function capability(): QualificationExecutorCapability {
  return {
    schema_version: 1,
    kind: "model_identity_qualification_executor",
    deployed_commit: "a".repeat(40),
    run_id: "33000000001",
    run_attempt: 1,
    journal_id: `mqj_${"b".repeat(64)}`,
    journal_revision: 3,
    operation: "owner_request",
    plan_digest: "c".repeat(64),
    request_digest: "d".repeat(64),
    request_index: 0,
    issued_at: NOW,
    expires_at: NOW + 60,
  };
}

function executorEnv(): ExecutorCloudflareEnv {
  const rateLimiter = env.API_RATE_LIMITER;
  if (rateLimiter === undefined) throw new Error("rate limiter binding is unavailable");
  const collision = env.MODEL_IDENTITY_QUALIFICATION_EXECUTOR;
  if (collision === undefined) throw new Error("test collision binding is unavailable");
  return {
    API_RATE_LIMITER: rateLimiter,
    AUTH_TOKEN_SECRET: "test-only-auth-token-secret-value-long-enough",
    DEPLOYED_COMMIT: "0000000000000000000000000000000000000000",
    DEPLOYMENT_ENVIRONMENT: "staging",
    GITHUB_STATE_TOKEN: "test-only-github-state-token-value-long-enough",
    MODEL_IDENTITY_QUALIFICATION_EXECUTOR_SECRET: SECRET,
    MODEL_IDENTITY_QUALIFICATION_COLLISION: collision,
    MODEL_IDENTITY_STATE_CONTRACT_COMMIT:
      "9fc7c431a92c678554c65ebac68d3fddf4990d29",
    STATE_REPOSITORY: "leanprover/lean-eval-state-staging",
  };
}

describe("model identity qualification executor capability", () => {
  it("binds every closed field and rejects tampering and expiry", async () => {
    const signed = await signQualificationExecutorCapability(SECRET, capability());
    await expect(verifyQualificationExecutorCapability(SECRET, signed, NOW + 1))
      .resolves.toEqual(capability());
    const pieces = signed.split(".");
    const signature = pieces[1];
    if (signature === undefined) throw new Error("capability signature is missing");
    const tampered = `${pieces[0] ?? ""}.${signature.slice(0, -1)}${signature.endsWith("a") ? "b" : "a"}`;
    await expect(verifyQualificationExecutorCapability(SECRET, tampered, NOW + 1))
      .rejects.toThrow("capability is invalid");
    await expect(verifyQualificationExecutorCapability(SECRET, signed, NOW + 61))
      .rejects.toThrow("capability is invalid");
    await expect(signQualificationExecutorCapability(SECRET, {
      ...capability(),
      operation: "unreviewed_operation",
    } as unknown as QualificationExecutorCapability)).rejects
      .toThrow("capability is invalid");
  });

  it("has no public route and fails closed before invoking a kernel", async () => {
    const publicResponse = await executor.fetch(
      new Request("https://executor.invalid/"),
      executorEnv(),
    );
    expect(publicResponse.status).toBe(404);
    const invalid = await executor.fetch(
      new Request("https://executor.invalid/internal/v1/execute", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          capability: "invalid",
          maintainer: { github_id: 3, login: "maintainer" },
          request: {},
          session: "x".repeat(32),
        }),
      }),
      executorEnv(),
    );
    expect(invalid.status).toBe(400);
    expect(await invalid.json()).toEqual({ error: "invalid_request" });

    for (const hostile of [{
      ...executorEnv(),
      DEPLOYMENT_ENVIRONMENT: "production" as const,
    }, {
      ...executorEnv(),
      STATE_REPOSITORY: "leanprover/lean-eval-state",
    }, {
      ...executorEnv(),
      MODEL_IDENTITY_STATE_CONTRACT_COMMIT: "f".repeat(40),
    }]) {
      const response = await executor.fetch(
        new Request("https://executor.invalid/internal/v1/execute", {
          method: "POST",
          body: "{}",
        }),
        hostile as unknown as ExecutorCloudflareEnv,
      );
      expect(response.status).toBe(404);
    }
  });
});
