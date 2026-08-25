import { verifyQualificationExecutorCapability } from "./model-identity-qualification-capability";

const STATE_REPOSITORY = "leanprover/lean-eval-state-staging";
const STATE_API = `https://api.github.com/repos/${STATE_REPOSITORY}`;
const INTERNAL_PATH = "/internal/v1/github";
const SHA = /^[0-9a-f]{40}$/;
const MAX_BODY_BYTES = 512 * 1024;

function json(body: unknown, status: number): Response {
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
    throw new TypeError("qualification collision value is invalid");
  }
  return value as Record<string, unknown>;
}

function exactFields(value: Record<string, unknown>, fields: readonly string[]): void {
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (
    actual.length !== expected.length ||
    actual.some((field, index) => field !== expected[index])
  ) throw new TypeError("qualification collision value is invalid");
}

async function responseJson(response: Response): Promise<Record<string, unknown>> {
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (!response.ok || bytes.byteLength > MAX_BODY_BYTES) {
    throw new TypeError("qualification collision provider response is invalid");
  }
  try {
    return object(JSON.parse(new TextDecoder().decode(bytes)) as unknown);
  } catch (error) {
    if (error instanceof TypeError) throw error;
    throw new TypeError("qualification collision provider response is invalid", {
      cause: error,
    });
  }
}

function upstreamHeaders(
  env: CollisionCloudflareEnv,
  request: Request,
): Headers {
  const headers = new Headers();
  for (const name of ["accept", "content-type", "user-agent", "x-github-api-version"]) {
    const value = request.headers.get(`x-lean-eval-upstream-${name}`);
    if (value !== null) headers.set(name, value);
  }
  headers.set("authorization", `Bearer ${env.GITHUB_STATE_TOKEN}`);
  return headers;
}

async function upstream(
  env: CollisionCloudflareEnv,
  path: string,
  method = "GET",
  body?: unknown,
): Promise<Response> {
  return fetch(`${STATE_API}${path}`, {
    method,
    redirect: "manual",
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${env.GITHUB_STATE_TOKEN}`,
      "content-type": "application/json",
      "user-agent": "lean-eval-model-identity-qualification-collision/1",
      "x-github-api-version": "2022-11-28",
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(10_000),
  });
}

async function sameTreeContender(
  env: CollisionCloudflareEnv,
  mutationCommit: string,
  message: string,
): Promise<string> {
  const mutation = await responseJson(
    await upstream(env, `/git/commits/${mutationCommit}`),
  );
  if (!Array.isArray(mutation.parents) || mutation.parents.length !== 1) {
    throw new TypeError("qualification collision mutation commit is invalid");
  }
  const parent = object(mutation.parents[0]);
  const parentSha = parent.sha;
  if (typeof parentSha !== "string" || !SHA.test(parentSha)) {
    throw new TypeError("qualification collision mutation parent is invalid");
  }
  const parentCommit = await responseJson(
    await upstream(env, `/git/commits/${parentSha}`),
  );
  const parentTree = object(parentCommit.tree).sha;
  if (typeof parentTree !== "string" || !SHA.test(parentTree)) {
    throw new TypeError("qualification collision parent tree is invalid");
  }
  const created = await responseJson(await upstream(env, "/git/commits", "POST", {
    message,
    tree: parentTree,
    parents: [parentSha],
  }));
  const contender = created.sha;
  if (typeof contender !== "string" || !SHA.test(contender)) {
    throw new TypeError("qualification collision contender is invalid");
  }
  const identity = await responseJson(
    await upstream(env, `/git/commits/${contender}`),
  );
  if (
    identity.sha !== contender ||
    identity.message !== message ||
    object(identity.tree).sha !== parentTree ||
    !Array.isArray(identity.parents) ||
    identity.parents.length !== 1 ||
    object(identity.parents[0]).sha !== parentSha
  ) throw new TypeError("qualification collision contender proof is invalid");
  const updated = await responseJson(
    await upstream(env, "/git/refs/heads/main", "PATCH", {
      sha: contender,
      force: false,
    }),
  );
  if (
    updated.ref !== "refs/heads/main" ||
    object(updated.object).sha !== contender
  ) throw new TypeError("qualification collision contender ref is invalid");
  return contender;
}

function exactStaging(env: CollisionCloudflareEnv): boolean {
  return SHA.test(env.DEPLOYED_COMMIT) &&
    exactString(env.DEPLOYMENT_ENVIRONMENT, "staging") &&
    exactString(env.STATE_REPOSITORY, STATE_REPOSITORY) &&
    new TextEncoder().encode(env.GITHUB_STATE_TOKEN).byteLength >= 32 &&
    new TextEncoder().encode(env.MODEL_IDENTITY_QUALIFICATION_EXECUTOR_SECRET)
      .byteLength >= 32;
}

function exactString(value: string, expected: string): boolean {
  return value === expected;
}

function upstreamPathValid(path: string): boolean {
  if (!path.startsWith("/") || path.includes("#") || path.includes("\\")) {
    return false;
  }
  try {
    const url = new URL(`${STATE_API}${path}`);
    return url.origin === "https://api.github.com" &&
      url.pathname.startsWith(`${new URL(STATE_API).pathname}/`) &&
      url.pathname.split("/").every((segment) => {
        const decoded = decodeURIComponent(segment);
        return decoded !== "." && decoded !== "..";
      });
  } catch {
    return false;
  }
}

function upstreamOperationValid(method: string, path: string): boolean {
  const pathname = new URL(`${STATE_API}${path}`).pathname
    .slice(new URL(STATE_API).pathname.length);
  return method === "GET" ||
    (method === "POST" && new Set([
      "/git/blobs",
      "/git/commits",
      "/git/trees",
    ]).has(pathname)) ||
    (method === "PATCH" && pathname === "/git/refs/heads/main");
}

export default {
  async fetch(request: Request, env: CollisionCloudflareEnv): Promise<Response> {
    if (
      request.method !== "POST" ||
      new URL(request.url).pathname !== INTERNAL_PATH ||
      !exactStaging(env)
    ) return json({ error: "not_found" }, 404);
    try {
      const capabilityToken = request.headers.get(
        "x-lean-eval-qualification-capability",
      );
      const method = request.headers.get("x-lean-eval-upstream-method");
      const path = request.headers.get("x-lean-eval-upstream-path");
      const attemptValue = request.headers.get("x-lean-eval-cas-attempt");
      if (
        capabilityToken === null ||
        method === null ||
        path === null ||
        attemptValue === null ||
        !/^[0-9]{1,3}$/.test(attemptValue) ||
        !upstreamPathValid(path) ||
        !upstreamOperationValid(method, path)
      ) throw new TypeError("qualification collision request is invalid");
      const capability = await verifyQualificationExecutorCapability(
        env.MODEL_IDENTITY_QUALIFICATION_EXECUTOR_SECRET,
        capabilityToken,
      );
      if (capability.deployed_commit !== env.DEPLOYED_COMMIT) {
        throw new TypeError("qualification collision capability is invalid");
      }
      const attempt = Number(attemptValue);
      const body = new Uint8Array(await request.arrayBuffer());
      if (body.byteLength > MAX_BODY_BYTES) {
        throw new TypeError("qualification collision request is too large");
      }
      if (
        capability.operation === "maximal_contention_measurement" &&
        method === "PATCH" &&
        path === "/git/refs/heads/main" &&
        attempt >= 1 &&
        attempt <= 7
      ) {
        const patch = object(
          JSON.parse(new TextDecoder().decode(body)) as unknown,
        );
        exactFields(patch, ["force", "sha"]);
        if (
          patch.force !== false ||
          typeof patch.sha !== "string" ||
          !SHA.test(patch.sha)
        ) throw new TypeError("qualification collision ref update is invalid");
        await sameTreeContender(
          env,
          patch.sha,
          `Model identity qualification collision ${capability.journal_id} revision ${String(capability.journal_revision)} attempt ${String(attempt)}`,
        );
      }
      const upstreamRequest = new Request(`${STATE_API}${path}`, {
        method,
        redirect: "manual",
        headers: upstreamHeaders(env, request),
        ...(body.byteLength === 0 ? {} : { body }),
        signal: AbortSignal.timeout(10_000),
      });
      return await fetch(upstreamRequest);
    } catch {
      return json({ error: "invalid_request" }, 400);
    }
  },
} satisfies ExportedHandler<CollisionCloudflareEnv>;
