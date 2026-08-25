import { describe, expect, it } from "vitest";

import { verifyGithubOidc } from "../src/replay-auth";

function base64Url(value: Uint8Array): string {
  let binary = "";
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function jsonPart(value: unknown): string {
  return base64Url(new TextEncoder().encode(JSON.stringify(value)));
}

async function signedRequest(
  overrides: Record<string, unknown> = {},
  path = "/api/v1/staging-acceptance",
): Promise<{
  request: Request;
  fetcher: typeof fetch;
}> {
  const keys = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true,
    ["sign", "verify"],
  );
  if (!("privateKey" in keys) || !("publicKey" in keys)) throw new Error("RSA fixture did not create a key pair");
  const now = 1_787_395_200;
  const sha = "a".repeat(40);
  const header = jsonPart({ alg: "RS256", kid: "fixture-key", typ: "JWT" });
  const claims = jsonPart({
    iss: "https://token.actions.githubusercontent.com",
    aud: "lean-eval-replay-staging",
    sub: "repo:leanprover/lean-eval-submissions:environment:replay-staging",
    repository: "leanprover/lean-eval-submissions",
    repository_id: "1243533004",
    repository_owner_id: "7233018",
    environment: "replay-staging",
    ref_protected: "true",
    ref: `refs/tags/lean-eval-dispatch/${sha}`,
    sha,
    iat: now - 30,
    nbf: now - 30,
    exp: now + 300,
    ...overrides,
  });
  const signature = await crypto.subtle.sign(
    "RSASSA-PKCS1-v1_5",
    keys.privateKey,
    new TextEncoder().encode(`${header}.${claims}`),
  );
  const token = `${header}.${claims}.${base64Url(new Uint8Array(signature))}`;
  const jwk = await crypto.subtle.exportKey("jwk", keys.publicKey);
  if (jwk instanceof ArrayBuffer) throw new Error("RSA fixture did not export as JWK");
  const fetcher: typeof fetch = () => Promise.resolve(Response.json({
    keys: [{
      kty: jwk.kty,
      n: jwk.n,
      e: jwk.e,
      alg: "RS256",
      kid: "fixture-key",
      use: "sig",
    }],
  }));
  return {
    request: new Request(`https://example.test${path}`, {
      method: "POST",
      headers: { authorization: `Bearer ${token}` },
    }),
    fetcher,
  };
}

const ENV = {
  DEPLOYED_COMMIT: "b".repeat(40),
  DEPLOYMENT_ENVIRONMENT: "staging",
  GITHUB_OIDC_AUDIENCE: "lean-eval-replay-staging",
  GITHUB_OIDC_ENVIRONMENT: "replay-staging",
};

const HISTORICAL_ENV = {
  DEPLOYED_COMMIT: "a".repeat(40),
  DEPLOYMENT_ENVIRONMENT: "production",
  GITHUB_OIDC_AUDIENCE: "lean-eval-historical-public-replay-production",
  GITHUB_OIDC_ENVIRONMENT: "replay-production",
};

const HISTORICAL_MAIN_CLAIMS = {
  aud: HISTORICAL_ENV.GITHUB_OIDC_AUDIENCE,
  sub: "repo:leanprover/lean-eval-submissions:environment:replay-production",
  environment: "replay-production",
  ref: "refs/heads/main",
  sha: HISTORICAL_ENV.DEPLOYED_COMMIT,
  workflow_ref: "leanprover/lean-eval-submissions/.github/workflows/"
    + "historical-authoritative-replay.yml@refs/heads/main",
  workflow_sha: HISTORICAL_ENV.DEPLOYED_COMMIT,
  event_name: "workflow_dispatch",
};

describe("GitHub OIDC replay authentication", () => {
  it("accepts only the protected environment and immutable dispatch tag", async () => {
    const { request, fetcher } = await signedRequest();
    await expect(verifyGithubOidc(request, ENV, fetcher, 1_787_395_200)).resolves.toBeUndefined();
  });

  it("rejects another environment, mutable ref, and bad signature", async () => {
    const wrongEnvironment = await signedRequest({ environment: "replay-production" });
    await expect(verifyGithubOidc(
      wrongEnvironment.request,
      ENV,
      wrongEnvironment.fetcher,
      1_787_395_200,
    )).rejects.toThrow("environment");

    const wrongRepositoryId = await signedRequest({ repository_id: "1243533005" });
    await expect(verifyGithubOidc(
      wrongRepositoryId.request,
      ENV,
      wrongRepositoryId.fetcher,
      1_787_395_200,
    )).rejects.toThrow("repository subject");

    const mutable = await signedRequest({ ref: "refs/heads/main" });
    await expect(verifyGithubOidc(mutable.request, ENV, mutable.fetcher, 1_787_395_200))
      .rejects.toThrow("immutable execution ref");

    const signed = await signedRequest();
    const authorization = signed.request.headers.get("authorization") ?? "";
    if (authorization.split(".").length !== 3) throw new Error("fixture authorization is invalid");
    const signatureOffset = authorization.lastIndexOf(".") + 1;
    const changedCharacter = authorization[signatureOffset] === "A" ? "B" : "A";
    const changed = `${authorization.slice(0, signatureOffset)}${changedCharacter}${authorization.slice(signatureOffset + 1)}`;
    await expect(verifyGithubOidc(new Request(signed.request, {
      headers: { authorization: changed },
    }), ENV, signed.fetcher, 1_787_395_200)).rejects.toThrow("signature");
  });

  it("accepts protected main only for the exact historical production workflow and routes", async () => {
    for (const path of [
      "/api/v1/historical-public-replay",
      "/api/v1/historical-public-replay/status",
      "/api/v1/historical-public-replay/cleanup",
      "/api/v1/historical-public-replay/cleanup-reservation",
    ]) {
      const { request, fetcher } = await signedRequest(HISTORICAL_MAIN_CLAIMS, path);
      await expect(verifyGithubOidc(request, HISTORICAL_ENV, fetcher, 1_787_395_200))
        .resolves.toBeUndefined();
    }
  });

  it("allows only the destructive cleanup route after protected main advances", async () => {
    const advancedClaims = {
      ...HISTORICAL_MAIN_CLAIMS,
      sha: "c".repeat(40),
      workflow_sha: "c".repeat(40),
    };
    const cleanup = await signedRequest(
      advancedClaims,
      "/api/v1/historical-public-replay/cleanup",
    );
    await expect(verifyGithubOidc(
      cleanup.request,
      HISTORICAL_ENV,
      cleanup.fetcher,
      1_787_395_200,
    )).resolves.toBeUndefined();

    for (const path of [
      "/api/v1/historical-public-replay",
      "/api/v1/historical-public-replay/status",
      "/api/v1/historical-public-replay/cleanup-reservation",
    ]) {
      const request = await signedRequest(advancedClaims, path);
      await expect(verifyGithubOidc(
        request.request,
        HISTORICAL_ENV,
        request.fetcher,
        1_787_395_200,
      )).rejects.toThrow("immutable execution ref");
    }
  });

  it("rejects hostile protected-main claim, route, deployment, and workflow drift", async () => {
    const cases: {
      label: string;
      overrides?: Record<string, unknown>;
      path?: string;
      env?: typeof HISTORICAL_ENV;
    }[] = [
      { label: "ordinary replay route", path: "/api/v1/replay" },
      { label: "health route", path: "/healthz" },
      {
        label: "ordinary production audience",
        env: {
          ...HISTORICAL_ENV,
          GITHUB_OIDC_AUDIENCE: "lean-eval-replay-production",
        },
      },
      {
        label: "staging deployment",
        env: { ...HISTORICAL_ENV, DEPLOYMENT_ENVIRONMENT: "staging" },
      },
      {
        label: "different deployed commit",
        env: { ...HISTORICAL_ENV, DEPLOYED_COMMIT: "b".repeat(40) },
      },
      { label: "different workflow", overrides: { workflow_ref: "other/workflow@refs/heads/main" } },
      { label: "different workflow sha", overrides: { workflow_sha: "b".repeat(40) } },
      { label: "non-dispatch event", overrides: { event_name: "push" } },
      { label: "unprotected ref", overrides: { ref_protected: "false" } },
      { label: "different repository", overrides: { repository: "leanprover/lean-eval" } },
    ];
    for (const item of cases) {
      const { request, fetcher } = await signedRequest(
        { ...HISTORICAL_MAIN_CLAIMS, ...item.overrides },
        item.path ?? "/api/v1/historical-public-replay",
      );
      await expect(
        verifyGithubOidc(request, item.env ?? HISTORICAL_ENV, fetcher, 1_787_395_200),
        item.label,
      ).rejects.toThrow();
    }

    const signedGet = await signedRequest(
      HISTORICAL_MAIN_CLAIMS,
      "/api/v1/historical-public-replay",
    );
    await expect(verifyGithubOidc(
      new Request(signedGet.request, { method: "GET" }),
      HISTORICAL_ENV,
      signedGet.fetcher,
      1_787_395_200,
    )).rejects.toThrow("immutable execution ref");
  });

  it("requires exact main for historical production while ordinary and staging stay tag-only", async () => {
    const historicalTag = await signedRequest({
      aud: HISTORICAL_ENV.GITHUB_OIDC_AUDIENCE,
      sub: "repo:leanprover/lean-eval-submissions:environment:replay-production",
      environment: "replay-production",
    }, "/api/v1/historical-public-replay");
    await expect(verifyGithubOidc(
      historicalTag.request,
      HISTORICAL_ENV,
      historicalTag.fetcher,
      1_787_395_200,
    )).rejects.toThrow("immutable execution ref");

    const ordinaryProduction = {
      ...HISTORICAL_ENV,
      GITHUB_OIDC_AUDIENCE: "lean-eval-replay-production",
    };
    const production = await signedRequest({
      ...HISTORICAL_MAIN_CLAIMS,
      aud: ordinaryProduction.GITHUB_OIDC_AUDIENCE,
    }, "/api/v1/replay");
    await expect(verifyGithubOidc(
      production.request,
      ordinaryProduction,
      production.fetcher,
      1_787_395_200,
    )).rejects.toThrow("immutable execution ref");

    const staging = await signedRequest({
      ref: "refs/heads/main",
      workflow_ref: HISTORICAL_MAIN_CLAIMS.workflow_ref,
      workflow_sha: "a".repeat(40),
      event_name: "workflow_dispatch",
    }, "/api/v1/historical-public-replay");
    await expect(verifyGithubOidc(staging.request, ENV, staging.fetcher, 1_787_395_200))
      .rejects.toThrow("immutable execution ref");
  });
});
