const GITHUB_ISSUER = "https://token.actions.githubusercontent.com";
const DISPATCH_REF = /^refs\/tags\/lean-eval-dispatch\/([0-9a-f]{40})$/;
const COMMIT = /^[0-9a-f]{40}$/;

type JwtHeader = { alg: string; kid: string; typ: string };
type JwtClaims = Record<string, unknown>;

export type ReplayAuthEnvironment = {
  GITHUB_OIDC_AUDIENCE: string;
  GITHUB_OIDC_ENVIRONMENT: string;
};

export class ReplayAuthError extends Error {}

function base64UrlBytes(value: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) throw new ReplayAuthError("token encoding is invalid");
  const padding = "=".repeat((4 - value.length % 4) % 4);
  const binary = atob(value.replaceAll("-", "+").replaceAll("_", "/") + padding);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function jsonPart(value: string, label: string): Record<string, unknown> {
  try {
    const decoded = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(base64UrlBytes(value));
    const parsed: unknown = JSON.parse(decoded);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error();
    return parsed as Record<string, unknown>;
  } catch (error) {
    if (error instanceof ReplayAuthError) throw error;
    throw new ReplayAuthError(`${label} is invalid`);
  }
}

function requiredString(value: unknown, label: string, maximum = 512): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum) {
    throw new ReplayAuthError(`${label} is invalid`);
  }
  return value;
}

function parseHeader(value: Record<string, unknown>): JwtHeader {
  if (value.alg !== "RS256" || value.typ !== "JWT") throw new ReplayAuthError("token algorithm is not allowed");
  return {
    alg: "RS256",
    typ: "JWT",
    kid: requiredString(value.kid, "token key ID", 256),
  };
}

async function githubKey(kid: string, fetcher: typeof fetch): Promise<JsonWebKey> {
  const response = await fetcher(`${GITHUB_ISSUER}/.well-known/jwks`, {
    headers: { accept: "application/json" },
    cf: { cacheEverything: true, cacheTtl: 3600 },
  });
  if (!response.ok) throw new ReplayAuthError("GitHub OIDC keys are unavailable");
  const raw: unknown = await response.json();
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new ReplayAuthError("GitHub OIDC keys are invalid");
  }
  const keys = (raw as Record<string, unknown>).keys;
  if (!Array.isArray(keys) || keys.length > 20) throw new ReplayAuthError("GitHub OIDC keys are invalid");
  let key: unknown;
  for (const candidate of keys as unknown[]) {
    if (typeof candidate === "object" && candidate !== null && !Array.isArray(candidate)
      && (candidate as Record<string, unknown>).kid === kid) {
      key = candidate;
      break;
    }
  }
  if (typeof key !== "object" || key === null || Array.isArray(key)) {
    throw new ReplayAuthError("token signing key is unknown");
  }
  const jwk = key as JsonWebKey;
  if (jwk.kty !== "RSA" || jwk.use !== "sig" || jwk.alg !== "RS256") {
    throw new ReplayAuthError("token signing key is not allowed");
  }
  return jwk;
}

function validateClaims(claims: JwtClaims, env: ReplayAuthEnvironment, nowSeconds: number): void {
  const repository = "leanprover/lean-eval-submissions";
  const expectedSubject = `repo:${repository}:environment:${env.GITHUB_OIDC_ENVIRONMENT}`;
  if (claims.iss !== GITHUB_ISSUER || claims.aud !== env.GITHUB_OIDC_AUDIENCE) {
    throw new ReplayAuthError("token issuer or audience is invalid");
  }
  if (claims.sub !== expectedSubject || claims.repository !== repository) {
    throw new ReplayAuthError("token repository subject is invalid");
  }
  if (claims.environment !== env.GITHUB_OIDC_ENVIRONMENT || claims.ref_protected !== "true") {
    throw new ReplayAuthError("token environment is not protected");
  }
  const ref = requiredString(claims.ref, "token ref");
  const sha = requiredString(claims.sha, "token sha", 40);
  const match = DISPATCH_REF.exec(ref);
  if (match?.[1] !== sha || !COMMIT.test(sha)) {
    throw new ReplayAuthError("token ref is not the immutable dispatch tag");
  }
  if (typeof claims.iat !== "number" || typeof claims.nbf !== "number" || typeof claims.exp !== "number") {
    throw new ReplayAuthError("token timestamps are invalid");
  }
  if (!Number.isSafeInteger(claims.iat) || !Number.isSafeInteger(claims.nbf) || !Number.isSafeInteger(claims.exp)) {
    throw new ReplayAuthError("token timestamps are invalid");
  }
  if (claims.nbf > nowSeconds + 30 || claims.exp < nowSeconds - 30 || claims.iat > nowSeconds + 30) {
    throw new ReplayAuthError("token is not currently valid");
  }
  if (claims.exp - claims.iat < 1 || claims.exp - claims.iat > 600) {
    throw new ReplayAuthError("token lifetime is invalid");
  }
}

export async function verifyGithubOidc(
  request: Request,
  env: ReplayAuthEnvironment,
  fetcher: typeof fetch = fetch,
  nowSeconds = Math.floor(Date.now() / 1000),
): Promise<void> {
  const authorization = request.headers.get("authorization");
  if (authorization === null || authorization.length > 8192 || !authorization.startsWith("Bearer ")) {
    throw new ReplayAuthError("bearer token is required");
  }
  const token = authorization.slice("Bearer ".length);
  const parts = token.split(".");
  if (parts.length !== 3) throw new ReplayAuthError("bearer token is malformed");
  const [encodedHeader, encodedClaims, encodedSignature] = parts;
  if (encodedHeader === undefined || encodedClaims === undefined || encodedSignature === undefined) {
    throw new ReplayAuthError("bearer token is malformed");
  }
  const header = parseHeader(jsonPart(encodedHeader, "token header"));
  const claims = jsonPart(encodedClaims, "token claims");
  validateClaims(claims, env, nowSeconds);
  const key = await crypto.subtle.importKey(
    "jwk",
    await githubKey(header.kid, fetcher),
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const verified = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    key,
    base64UrlBytes(encodedSignature),
    new TextEncoder().encode(`${encodedHeader}.${encodedClaims}`),
  );
  if (!verified) throw new ReplayAuthError("token signature is invalid");
}
