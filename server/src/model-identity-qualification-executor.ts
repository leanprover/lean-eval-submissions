import {
  executeModelIdentityQualificationKernel,
  type RuntimeEnv,
} from "./app";
import type { GitHubFetch } from "./github-state";
import {
  qualificationApiRequestDigest,
  type QualificationApiRequestPlan,
} from "./model-identity-qualification-journal";
import { verifyQualificationExecutorCapability } from "./model-identity-qualification-capability";
import { STAGING_MODEL_IDENTITY_STATE_CONTRACT_COMMIT } from "./model-identity";

const MAX_REQUEST_BYTES = 32 * 1024;
const SHA = /^[0-9a-f]{40}$/;
const STATE_REPOSITORY = "leanprover/lean-eval-state-staging";
const STATE_API_PREFIX = `/repos/${STATE_REPOSITORY}`;

type ExecutionMeasurements = {
  subrequests: number;
  gitObjectWrites: number;
  casAttempts: number;
};

type ExecutorIdentity = Readonly<{ github_id: number; login: string }>;

function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: {
      "cache-control": "no-store",
      "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
      "referrer-policy": "no-referrer",
      "x-content-type-options": "nosniff",
    },
  });
}

function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("qualification executor request is invalid");
  }
  return value as Record<string, unknown>;
}

function exactFields(value: Record<string, unknown>, fields: readonly string[]): void {
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (
    actual.length !== expected.length ||
    actual.some((field, index) => field !== expected[index])
  ) throw new TypeError("qualification executor request is invalid");
}

function identity(value: unknown): ExecutorIdentity {
  const input = object(value);
  exactFields(input, ["github_id", "login"]);
  if (
    typeof input.github_id !== "number" ||
    !Number.isSafeInteger(input.github_id) ||
    input.github_id < 1 ||
    typeof input.login !== "string" ||
    !/^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?$/.test(input.login)
  ) throw new TypeError("qualification executor identity is invalid");
  return { github_id: input.github_id, login: input.login };
}

async function requestBody(request: Request): Promise<Record<string, unknown>> {
  const bytes = new Uint8Array(await request.arrayBuffer());
  if (bytes.byteLength > MAX_REQUEST_BYTES) {
    throw new TypeError("qualification executor request is too large");
  }
  try {
    return object(JSON.parse(new TextDecoder().decode(bytes)) as unknown);
  } catch (error) {
    if (error instanceof TypeError) throw error;
    throw new TypeError("qualification executor request is invalid", { cause: error });
  }
}

function exactStaging(env: ExecutorCloudflareEnv): boolean {
  return SHA.test(env.DEPLOYED_COMMIT) &&
    new TextEncoder().encode(env.AUTH_TOKEN_SECRET).byteLength >= 32 &&
    new TextEncoder().encode(env.GITHUB_STATE_TOKEN).byteLength >= 32 &&
    new TextEncoder().encode(env.MODEL_IDENTITY_QUALIFICATION_EXECUTOR_SECRET)
      .byteLength >= 32;
}

function runtime(env: ExecutorCloudflareEnv): RuntimeEnv {
  return {
    API_RATE_LIMITER: env.API_RATE_LIMITER,
    AUTH_TOKEN_SECRET: env.AUTH_TOKEN_SECRET,
    DEPLOYED_COMMIT: env.DEPLOYED_COMMIT,
    DEPLOYMENT_ENVIRONMENT: "staging",
    GITHUB_STATE_TOKEN: env.GITHUB_STATE_TOKEN,
    INTAKE_ENABLED: "false",
    INTAKE_ENABLEMENT_MODE: "disabled",
    MODEL_IDENTITY_MAINTAINER_API_ENABLED: "true",
    MODEL_IDENTITY_OWNER_API_ENABLED: "true",
    MODEL_IDENTITY_STATE_CONTRACT_COMMIT:
      STAGING_MODEL_IDENTITY_STATE_CONTRACT_COMMIT,
    STATE_REPOSITORY,
  };
}

function measuredStateFetch(
  env: ExecutorCloudflareEnv,
  capability: string,
  measurements: ExecutionMeasurements,
): GitHubFetch {
  return async (input, init) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    const method = request.method.toUpperCase();
    if (
      url.protocol !== "https:" ||
      url.hostname !== "api.github.com" ||
      !url.pathname.startsWith(`${STATE_API_PREFIX}/`)
    ) throw new TypeError("qualification executor State request is invalid");
    measurements.subrequests += 1;
    const statePath = url.pathname.slice(STATE_API_PREFIX.length);
    if (
      method === "POST" &&
      new Set(["/git/blobs", "/git/trees", "/git/commits"]).has(statePath)
    ) measurements.gitObjectWrites += 1;
    if (method === "PATCH" && statePath === "/git/refs/heads/main") {
      measurements.casAttempts += 1;
    }
    const headers = new Headers({
      "x-lean-eval-cas-attempt": String(measurements.casAttempts),
      "x-lean-eval-qualification-capability": capability,
      "x-lean-eval-upstream-method": method,
      "x-lean-eval-upstream-path": `${statePath}${url.search}`,
    });
    for (const name of ["accept", "content-type", "user-agent", "x-github-api-version"]) {
      const value = request.headers.get(name);
      if (value !== null) headers.set(`x-lean-eval-upstream-${name}`, value);
    }
    const body = new Uint8Array(await request.arrayBuffer());
    return env.MODEL_IDENTITY_QUALIFICATION_COLLISION.fetch(
      "https://qualification-collision.invalid/internal/v1/github",
      {
        method: "POST",
        headers,
        ...(body.byteLength === 0 ? {} : { body }),
      },
    );
  };
}

function measuredResponse(
  response: Response,
  measurements: ExecutionMeasurements,
): Response {
  const headers = new Headers(response.headers);
  headers.set(
    "x-lean-eval-qualification-subrequests",
    String(measurements.subrequests),
  );
  headers.set(
    "x-lean-eval-qualification-git-object-writes",
    String(measurements.gitObjectWrites),
  );
  headers.set(
    "x-lean-eval-qualification-cas-attempts",
    String(measurements.casAttempts),
  );
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

export default {
  async fetch(request, env): Promise<Response> {
    if (
      request.method !== "POST" ||
      new URL(request.url).pathname !== "/internal/v1/execute" ||
      !exactStaging(env)
    ) return json({ error: "not_found" }, 404);
    try {
      const body = await requestBody(request);
      exactFields(body, ["capability", "maintainer", "request", "session"]);
      if (
        typeof body.capability !== "string" ||
        typeof body.session !== "string" ||
        new TextEncoder().encode(body.session).byteLength < 32 ||
        new TextEncoder().encode(body.session).byteLength > 4096
      ) throw new TypeError("qualification executor request is invalid");
      const capability = await verifyQualificationExecutorCapability(
        env.MODEL_IDENTITY_QUALIFICATION_EXECUTOR_SECRET,
        body.capability,
      );
      const operation = body.request as QualificationApiRequestPlan;
      if (
        capability.deployed_commit !== env.DEPLOYED_COMMIT ||
        capability.request_digest !== await qualificationApiRequestDigest(operation)
      ) throw new TypeError("qualification executor capability is invalid");
      const maintainer = identity(body.maintainer);
      const kernelRequest = new Request(
        `https://qualification-executor.invalid${operation.path}`,
        {
          method: operation.method,
          headers: {
            authorization: `Bearer ${body.session}`,
            "content-type": "application/json",
            "idempotency-key": operation.event_id,
          },
          body: JSON.stringify(operation.body),
        },
      );
      const measurements: ExecutionMeasurements = {
        subrequests: 0,
        gitObjectWrites: 0,
        casAttempts: 0,
      };
      const response = await executeModelIdentityQualificationKernel(
        kernelRequest,
        runtime(env),
        { stateFetch: measuredStateFetch(env, body.capability, measurements) },
        maintainer,
        Date.parse(operation.occurred_at),
      );
      return measuredResponse(response, measurements);
    } catch {
      return json({ error: "invalid_request" }, 400);
    }
  },
} satisfies ExportedHandler<ExecutorCloudflareEnv>;
