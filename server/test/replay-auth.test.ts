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

async function signedRequest(overrides: Record<string, unknown> = {}): Promise<{
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
    request: new Request("https://example.test/api/v1/staging-acceptance", {
      method: "POST",
      headers: { authorization: `Bearer ${token}` },
    }),
    fetcher,
  };
}

const ENV = {
  GITHUB_OIDC_AUDIENCE: "lean-eval-replay-staging",
  GITHUB_OIDC_ENVIRONMENT: "replay-staging",
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
      .rejects.toThrow("immutable dispatch tag");

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
});
