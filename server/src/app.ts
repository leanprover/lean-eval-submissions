import {
  type GitHubFetch,
  GitHubStateError,
  GitHubStateRepository,
} from "./github-state";

export type RuntimeEnv = Omit<
  CloudflareEnv,
  | "DEPLOYED_COMMIT"
  | "DEPLOYMENT_ENVIRONMENT"
  | "INTAKE_ENABLED"
  | "STATE_REPOSITORY"
> &
  Readonly<{
    DEPLOYED_COMMIT: string;
    DEPLOYMENT_ENVIRONMENT: "staging" | "production";
    GITHUB_STATE_TOKEN?: string;
    INTAKE_ENABLED: string;
    READINESS_TOKEN?: string;
    STATE_REPOSITORY: string;
  }>;

type Lifecycle = Pick<ExecutionContext, "waitUntil">;

const JSON_HEADERS = {
  "cache-control": "no-store",
  "content-type": "application/json; charset=utf-8",
  "x-content-type-options": "nosniff",
} as const;

function json(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: JSON_HEADERS });
}

function intakeEnabled(env: RuntimeEnv): boolean {
  return env.INTAKE_ENABLED === "true";
}

async function equalSecret(actual: string, expected: string): Promise<boolean> {
  const encoder = new TextEncoder();
  const [actualDigest, expectedDigest] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(actual)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  const left = new Uint8Array(actualDigest);
  const right = new Uint8Array(expectedDigest);
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= (left[index] ?? 0) ^ (right[index] ?? 0);
  }
  return difference === 0;
}

async function readinessAuthorized(request: Request, env: RuntimeEnv): Promise<boolean> {
  if (!env.READINESS_TOKEN) return false;
  const header = request.headers.get("authorization");
  if (!header?.startsWith("Bearer ")) return false;
  return equalSecret(header.slice("Bearer ".length), env.READINESS_TOKEN);
}

function readinessCacheKey(env: RuntimeEnv): Request {
  return new Request(`https://readiness.invalid/${env.DEPLOYMENT_ENVIRONMENT}`);
}

async function cachedReadiness(env: RuntimeEnv): Promise<Response | null> {
  try {
    const cached = await caches.default.match(readinessCacheKey(env));
    if (!cached) return null;
    return new Response(cached.body, {
      status: cached.status,
      headers: JSON_HEADERS,
    });
  } catch {
    return null;
  }
}

function cacheReadiness(env: RuntimeEnv, response: Response, lifecycle: Lifecycle): void {
  const cached = response.clone();
  cached.headers.set("cache-control", "public, max-age=15");
  lifecycle.waitUntil(
    caches.default.put(readinessCacheKey(env), cached).catch(() => undefined),
  );
}

const timedGitHubFetch: GitHubFetch = (input, init) =>
  fetch(input, { ...init, signal: AbortSignal.timeout(5000) });

async function readiness(
  request: Request,
  env: RuntimeEnv,
  lifecycle: Lifecycle,
): Promise<Response> {
  if (!(await readinessAuthorized(request, env))) return json({ error: "not_found" }, 404);
  if (!intakeEnabled(env)) {
    return json(
      {
        status: "not_ready",
        reason: "intake_disabled",
        environment: env.DEPLOYMENT_ENVIRONMENT,
      },
      503,
    );
  }
  if (!env.GITHUB_STATE_TOKEN) {
    return json({ status: "not_ready", reason: "state_credential_missing" }, 503);
  }
  const cached = await cachedReadiness(env);
  if (cached) return cached;
  try {
    await new GitHubStateRepository(
      {
        repository: env.STATE_REPOSITORY,
        token: env.GITHUB_STATE_TOKEN,
        userAgent: "lean-eval-submission-worker",
      },
      timedGitHubFetch,
    ).assertAvailable();
    const response = json({ status: "ready", environment: env.DEPLOYMENT_ENVIRONMENT });
    cacheReadiness(env, response, lifecycle);
    return response;
  } catch (error) {
    console.error(
      JSON.stringify({
        event: "state_readiness_failed",
        environment: env.DEPLOYMENT_ENVIRONMENT,
        error_name: error instanceof Error ? error.name : "unknown",
        upstream_status: error instanceof GitHubStateError ? error.status : null,
      }),
    );
    const response = json({ status: "not_ready", reason: "state_unavailable" }, 503);
    cacheReadiness(env, response, lifecycle);
    return response;
  }
}

export async function handleRequest(
  request: Request,
  env: RuntimeEnv,
  lifecycle: Lifecycle,
): Promise<Response> {
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/healthz") {
    return json({
      status: "ok",
      service: "lean-eval-submission",
      deployed_commit: env.DEPLOYED_COMMIT,
      environment: env.DEPLOYMENT_ENVIRONMENT,
      intake_enabled: intakeEnabled(env),
    });
  }
  if (request.method === "GET" && url.pathname === "/readyz") {
    return readiness(request, env, lifecycle);
  }
  if (url.pathname.startsWith("/api/") && !intakeEnabled(env)) {
    return json({ error: "intake_disabled" }, 503);
  }
  return json({ error: "not_found" }, 404);
}
