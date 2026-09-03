const GITHUB_ISSUER = "https://token.actions.githubusercontent.com";
const GITHUB_REPOSITORY = "leanprover/lean-eval-submissions";
const GITHUB_REPOSITORY_ID = "1243533004";
const GITHUB_OWNER_ID = "7233018";
const DISPATCH_REF = /^refs\/tags\/lean-eval-dispatch\/([0-9a-f]{40})$/;
const COMMIT = /^[0-9a-f]{40}$/;
const HISTORICAL_PRODUCTION_AUDIENCE = "lean-eval-historical-public-replay-production";
const HISTORICAL_PRODUCTION_ENVIRONMENT = "replay-production";
const HISTORICAL_WORKFLOW_REF = `${GITHUB_REPOSITORY}/.github/workflows/`
  + "historical-authoritative-replay.yml@refs/heads/main";
const HISTORICAL_ROUTES = new Set([
  "/api/v1/historical-public-replay",
  "/api/v1/historical-public-replay/status",
  "/api/v1/historical-public-replay/cleanup",
  "/api/v1/historical-public-replay/cleanup-reservation",
]);
const HISTORICAL_CLEANUP_ROUTE = "/api/v1/historical-public-replay/cleanup";
const HISTORICAL_PRIVATE_AUDIENCE = "lean-eval-historical-private-replay";
const HISTORICAL_PRIVATE_ENVIRONMENT = "replay-production";
const HISTORICAL_PRIVATE_WORKFLOW_REF = `${GITHUB_REPOSITORY}/.github/workflows/`
  + "historical-private-replay.yml@refs/heads/main";
const HISTORICAL_PRIVATE_ROUTES = new Set([
  "/api/v1/replay",
  "/api/v1/replay/status",
  "/api/v1/historical-private-replay/prewarm",
  "/api/v1/historical-private-replay/reserve",
  "/api/v1/historical-private-replay/cleanup",
]);
const HISTORICAL_PRIVATE_CLEANUP_ROUTE = "/api/v1/historical-private-replay/cleanup";

type JwtHeader = { alg: string; kid: string; typ: string };
type JwtClaims = Record<string, unknown>;
export type ReplayAuthEnvironment = {
  DEPLOYED_COMMIT: string;
  DEPLOYMENT_ENVIRONMENT: string;
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

function isHistoricalProductionSurface(
  request: Request,
  env: ReplayAuthEnvironment,
): boolean {
  return request.method === "POST"
    && HISTORICAL_ROUTES.has(new URL(request.url).pathname)
    && env.DEPLOYMENT_ENVIRONMENT === "production"
    && env.GITHUB_OIDC_AUDIENCE === HISTORICAL_PRODUCTION_AUDIENCE
    && env.GITHUB_OIDC_ENVIRONMENT === HISTORICAL_PRODUCTION_ENVIRONMENT;
}

function isHistoricalPrivateSurface(
  request: Request,
  env: ReplayAuthEnvironment,
): boolean {
  return request.method === "POST"
    && HISTORICAL_PRIVATE_ROUTES.has(new URL(request.url).pathname)
    && env.DEPLOYMENT_ENVIRONMENT === "historical-private-replay"
    && env.GITHUB_OIDC_AUDIENCE === HISTORICAL_PRIVATE_AUDIENCE
    && env.GITHUB_OIDC_ENVIRONMENT === HISTORICAL_PRIVATE_ENVIRONMENT;
}

function allowsHistoricalProtectedMain(
  request: Request,
  claims: JwtClaims,
  env: ReplayAuthEnvironment,
  ref: string,
  sha: string,
): boolean {
  const exactProtectedWorkflow = isHistoricalProductionSurface(request, env)
    && ref === "refs/heads/main"
    && COMMIT.test(sha)
    && claims.workflow_ref === HISTORICAL_WORKFLOW_REF
    && claims.workflow_sha === sha
    && claims.event_name === "workflow_dispatch";
  if (!exactProtectedWorkflow) return false;
  // Cleanup can only destroy the sandbox bound inside the receipt object. It
  // must remain callable by a later protected-main controller after main has
  // advanced; start and status retain the exact deployed-commit boundary.
  if (new URL(request.url).pathname === HISTORICAL_CLEANUP_ROUTE) return true;
  return COMMIT.test(env.DEPLOYED_COMMIT) && sha === env.DEPLOYED_COMMIT;
}

function allowsHistoricalPrivateProtectedMain(
  request: Request,
  claims: JwtClaims,
  env: ReplayAuthEnvironment,
  ref: string,
  sha: string,
): boolean {
  const exactProtectedWorkflow = isHistoricalPrivateSurface(request, env)
    && ref === "refs/heads/main"
    && COMMIT.test(sha)
    && claims.workflow_ref === HISTORICAL_PRIVATE_WORKFLOW_REF
    && claims.workflow_sha === sha
    && claims.event_name === "workflow_dispatch";
  if (!exactProtectedWorkflow) return false;
  if (new URL(request.url).pathname === HISTORICAL_PRIVATE_CLEANUP_ROUTE) {
    return true;
  }
  return COMMIT.test(env.DEPLOYED_COMMIT) && sha === env.DEPLOYED_COMMIT;
}

function validateClaims(
  request: Request,
  claims: JwtClaims,
  env: ReplayAuthEnvironment,
  nowSeconds: number,
): void {
  const expectedSubject = `repo:${GITHUB_REPOSITORY}:environment:${env.GITHUB_OIDC_ENVIRONMENT}`;
  if (claims.iss !== GITHUB_ISSUER || claims.aud !== env.GITHUB_OIDC_AUDIENCE) {
    throw new ReplayAuthError("token issuer or audience is invalid");
  }
  if (
    claims.sub !== expectedSubject ||
    claims.repository !== GITHUB_REPOSITORY ||
    claims.repository_id !== GITHUB_REPOSITORY_ID ||
    claims.repository_owner_id !== GITHUB_OWNER_ID
  ) {
    throw new ReplayAuthError("token repository subject is invalid");
  }
  if (claims.environment !== env.GITHUB_OIDC_ENVIRONMENT || claims.ref_protected !== "true") {
    throw new ReplayAuthError("token environment is not protected");
  }
  const ref = requiredString(claims.ref, "token ref");
  const sha = requiredString(claims.sha, "token sha", 40);
  const match = DISPATCH_REF.exec(ref);
  const immutableDispatch = match?.[1] === sha && COMMIT.test(sha);
  const refAllowed = isHistoricalProductionSurface(request, env)
    ? allowsHistoricalProtectedMain(request, claims, env, ref, sha)
    : isHistoricalPrivateSurface(request, env)
      ? allowsHistoricalPrivateProtectedMain(request, claims, env, ref, sha)
      : immutableDispatch;
  if (!refAllowed) {
    throw new ReplayAuthError("token ref is not an allowed immutable execution ref");
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

async function verifyGithubOidcWithMode(
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
  validateClaims(request, claims, env, nowSeconds);
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

export function verifyGithubOidc(
  request: Request,
  env: ReplayAuthEnvironment,
  fetcher: typeof fetch = fetch,
  nowSeconds = Math.floor(Date.now() / 1000),
): Promise<void> {
  return verifyGithubOidcWithMode(request, env, fetcher, nowSeconds);
}
