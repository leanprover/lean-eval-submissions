import type { Sandbox } from "@cloudflare/sandbox";

import { ReplayAuthError, type ReplayAuthEnvironment, verifyGithubOidc } from "./replay-auth";
import {
  ReplayContractError,
  readAcceptanceRequest,
  validateSandboxEvidence,
} from "./replay-contract";

export type ReplayRuntimeEnv = ReplayAuthEnvironment & {
  REPLAY_SANDBOX: DurableObjectNamespace<Sandbox>;
  DEPLOYED_COMMIT: string;
  DEPLOYMENT_ENVIRONMENT: string;
  REPLAY_ENABLED: string;
  STAGING_ACCEPTANCE_ENABLED: string;
  STAGING_MEMORY_LIMIT_BYTES: string;
  PRODUCTION_MEMORY_GATE_BYTES: string;
};

type SandboxClient = Pick<Sandbox, "writeFile" | "exec" | "destroy">;

type Dependencies = {
  authenticate(request: Request, env: ReplayAuthEnvironment): Promise<void>;
  sandbox(env: ReplayRuntimeEnv, runnerNonce: string): SandboxClient;
};

const DEFAULT_DEPENDENCIES: Dependencies = {
  authenticate: verifyGithubOidc,
  sandbox(): SandboxClient {
    throw new Error("sandbox dependency was not configured");
  },
};

function json(value: unknown, status = 200): Response {
  return Response.json(value, { status, headers: { "cache-control": "no-store" } });
}

function health(env: ReplayRuntimeEnv): Response {
  return json({
    status: "ok",
    service: "lean-eval-replay-executor",
    environment: env.DEPLOYMENT_ENVIRONMENT,
    deployed_commit: env.DEPLOYED_COMMIT,
    replay_enabled: env.REPLAY_ENABLED === "true",
    staging_acceptance_enabled: env.STAGING_ACCEPTANCE_ENABLED === "true",
    staging_memory_limit_bytes: Number(env.STAGING_MEMORY_LIMIT_BYTES),
    production_memory_gate_bytes: Number(env.PRODUCTION_MEMORY_GATE_BYTES),
  });
}

export async function handleReplayRequest(
  request: Request,
  env: ReplayRuntimeEnv,
  dependencies: Dependencies = DEFAULT_DEPENDENCIES,
): Promise<Response> {
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/healthz") return health(env);
  if (url.pathname !== "/api/v1/staging-acceptance" || request.method !== "POST") {
    return json({ error: "not_found" }, 404);
  }
  if (env.DEPLOYMENT_ENVIRONMENT !== "staging" || env.STAGING_ACCEPTANCE_ENABLED !== "true") {
    return json({ error: "staging_acceptance_disabled" }, 503);
  }
  try {
    await dependencies.authenticate(request, env);
    const input = await readAcceptanceRequest(request);
    const sandbox = dependencies.sandbox(env, input.runner_nonce);
    const evidence = await (async () => {
      try {
        await sandbox.writeFile("/workspace/archive.tar.gz.age.b64", input.ciphertext_base64);
        await sandbox.writeFile("/workspace/identity.age.b64", input.plaintext_identity_base64);
        await sandbox.writeFile(
          "/workspace/expectation.json",
          JSON.stringify({
            schema_version: 1,
            archive_ciphertext_sha256: input.archive_ciphertext_sha256,
            marker_sha256: input.marker_sha256,
          }),
        );
        const result = await sandbox.exec("/opt/lean-eval/replay-staging-acceptance", { timeout: 120_000 });
        if (!result.success || result.stdout.length > 4096) {
          throw new Error("sandbox acceptance command failed");
        }
        try {
          return validateSandboxEvidence(JSON.parse(result.stdout) as unknown, input);
        } catch {
          throw new Error("sandbox acceptance evidence was invalid");
        }
      } finally {
        await sandbox.destroy();
      }
    })();
    return json({
      schema_version: 1,
      service: "lean-eval-replay-executor",
      environment: "staging",
      request_id: input.request_id,
      runner_nonce: input.runner_nonce,
      archive_ciphertext_sha256: evidence.archive_ciphertext_sha256,
      marker_sha256: evidence.marker_sha256,
      network_policy: "disabled",
      network_probe: evidence.network_probe,
      destruction: "confirmed",
      architecture: evidence.architecture,
      kernel_release: evidence.kernel_release,
      cpu_model: evidence.cpu_model,
      staging_memory_limit_bytes: Number(env.STAGING_MEMORY_LIMIT_BYTES),
      production_memory_gate_bytes: Number(env.PRODUCTION_MEMORY_GATE_BYTES),
    });
  } catch (error) {
    if (error instanceof ReplayAuthError) return json({ error: "unauthorized" }, 401);
    if (error instanceof ReplayContractError || error instanceof SyntaxError) {
      return json({ error: "invalid_request" }, 400);
    }
    return json({ error: "executor_failed" }, 500);
  }
}
