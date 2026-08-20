import { GitHubStateRepository } from "./github-state";

export type RuntimeEnv = Omit<
  CloudflareEnv,
  "DEPLOYMENT_ENVIRONMENT" | "INTAKE_ENABLED" | "STATE_REPOSITORY"
> &
  Readonly<{
    DEPLOYMENT_ENVIRONMENT: "staging" | "production";
    GITHUB_STATE_TOKEN?: string;
    INTAKE_ENABLED: string;
    STATE_REPOSITORY: string;
  }>;

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

async function readiness(env: RuntimeEnv): Promise<Response> {
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
  try {
    await new GitHubStateRepository({
      repository: env.STATE_REPOSITORY,
      token: env.GITHUB_STATE_TOKEN,
      userAgent: "lean-eval-submission-worker",
    }).assertAvailable();
    return json({ status: "ready", environment: env.DEPLOYMENT_ENVIRONMENT });
  } catch (error) {
    console.error("State readiness check failed", error);
    return json({ status: "not_ready", reason: "state_unavailable" }, 503);
  }
}

export async function handleRequest(request: Request, env: RuntimeEnv): Promise<Response> {
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/healthz") {
    return json({
      status: "ok",
      service: "lean-eval-submission",
      environment: env.DEPLOYMENT_ENVIRONMENT,
      intake_enabled: intakeEnabled(env),
    });
  }
  if (request.method === "GET" && url.pathname === "/readyz") return readiness(env);
  if (url.pathname.startsWith("/api/") && !intakeEnabled(env)) {
    return json({ error: "intake_disabled" }, 503);
  }
  return json({ error: "not_found" }, 404);
}
