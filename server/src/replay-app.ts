import type { Sandbox } from "@cloudflare/sandbox";

import {
  AuthoritativeReplayContractError,
  readAuthoritativeReplayRequest,
  validateReplayVerdict,
} from "./authoritative-replay-contract";
import { ReplayAuthError, type ReplayAuthEnvironment, verifyGithubOidc } from "./replay-auth";
import {
  ReplayArchiveContractError,
  readArchiveAcceptanceRequest,
  validateArchiveEvidence,
} from "./replay-archive-contract";
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
  REVIEWED_EXECUTION_PROFILE_DIGEST: string;
  REVIEWED_MEASUREMENT_CONFIG_DIGEST: string;
  REVIEWED_VM_IMAGE_DIGEST: string;
};

type SandboxClient = Pick<Sandbox, "writeFile" | "exec" | "destroy">;

type ExecutorFailureReason =
  | "input_transfer_failed"
  | "command_rpc_failed"
  | "command_failed"
  | "command_output_invalid"
  | "sandbox_destroy_failed"
  | "unexpected_failure";

class ReplayExecutorError extends Error {
  constructor(
    readonly reason: ExecutorFailureReason,
    readonly detail?: string,
  ) {
    super(reason);
  }
}

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

const ARCHIVE_COMMAND_FAILURES = new Map([
  ["expectation is invalid", "expectation_invalid"],
  ["expectation fields are invalid", "expectation_fields_invalid"],
  ["expectation schema is invalid", "expectation_schema_invalid"],
  ["encoded input is invalid", "encoded_input_invalid"],
  ["decoded input exceeds size limit", "decoded_input_too_large"],
  ["ciphertext digest mismatch", "ciphertext_digest_mismatch"],
  ["archive decryption failed", "archive_decryption_failed"],
  ["plaintext size mismatch", "plaintext_size_mismatch"],
  ["plaintext digest mismatch", "plaintext_digest_mismatch"],
  ["decrypted archive is invalid", "archive_invalid"],
  ["decrypted archive member count is invalid", "archive_member_count_invalid"],
  ["decrypted archive contains an unsafe member", "archive_member_unsafe"],
  ["decrypted archive expands beyond its limit", "archive_expansion_too_large"],
  ["network isolation failed", "network_isolation_failed"],
]);

function safeCommandFailureDetail(command: string, stderr: string): string | undefined {
  if (command !== "/opt/lean-eval/replay-archive-acceptance") return undefined;
  return ARCHIVE_COMMAND_FAILURES.get(stderr.trim()) ?? "unclassified_archive_failure";
}

async function writeSandboxFile(
  sandbox: SandboxClient,
  path: string,
  contents: string,
): Promise<void> {
  let result: Awaited<ReturnType<SandboxClient["writeFile"]>>;
  try {
    result = await sandbox.writeFile(path, contents);
  } catch {
    throw new ReplayExecutorError("input_transfer_failed");
  }
  if (!result.success || result.path !== path) {
    throw new ReplayExecutorError("input_transfer_failed");
  }
}

async function executeSandboxCommand(
  sandbox: SandboxClient,
  command: string,
  timeout: number,
  maximumStdout: number,
): Promise<string> {
  let result: Awaited<ReturnType<SandboxClient["exec"]>>;
  try {
    result = await sandbox.exec(command, { timeout });
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  if (!result.success) {
    throw new ReplayExecutorError(
      "command_failed",
      safeCommandFailureDetail(command, result.stderr),
    );
  }
  if (result.stdout.length > maximumStdout) {
    throw new ReplayExecutorError("command_output_invalid");
  }
  return result.stdout;
}

async function withSandboxDestruction<T>(
  sandbox: SandboxClient,
  operation: () => Promise<T>,
): Promise<T> {
  let outcome: { ok: true; value: T } | { ok: false; error: unknown };
  try {
    outcome = { ok: true, value: await operation() };
  } catch (error) {
    outcome = { ok: false, error };
  }
  let destructionFailed = false;
  try {
    await sandbox.destroy();
  } catch {
    destructionFailed = true;
  }
  if (!outcome.ok) throw outcome.error;
  if (destructionFailed) throw new ReplayExecutorError("sandbox_destroy_failed");
  return outcome.value;
}

function recordExecutorFailure(route: string, error: unknown): void {
  const reason = error instanceof ReplayExecutorError ? error.reason : "unexpected_failure";
  const detail = error instanceof ReplayExecutorError ? error.detail : undefined;
  console.error(JSON.stringify({
    event: "lean_eval_replay_executor_failure",
    route,
    reason,
    ...(detail === undefined ? {} : { detail }),
  }));
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
    reviewed_execution_profile_digest: env.REVIEWED_EXECUTION_PROFILE_DIGEST,
    reviewed_measurement_config_digest: env.REVIEWED_MEASUREMENT_CONFIG_DIGEST,
    reviewed_vm_image_digest: env.REVIEWED_VM_IMAGE_DIGEST,
  });
}

export async function handleReplayRequest(
  request: Request,
  env: ReplayRuntimeEnv,
  dependencies: Dependencies = DEFAULT_DEPENDENCIES,
): Promise<Response> {
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/healthz") return health(env);
  const syntheticAcceptance = url.pathname === "/api/v1/staging-acceptance";
  const archiveAcceptance = url.pathname === "/api/v1/staging-archive-acceptance";
  const authoritativeReplay = url.pathname === "/api/v1/replay";
  if ((!syntheticAcceptance && !archiveAcceptance && !authoritativeReplay) || request.method !== "POST") {
    return json({ error: "not_found" }, 404);
  }
  if (authoritativeReplay && env.REPLAY_ENABLED !== "true") {
    return json({ error: "replay_disabled" }, 503);
  }
  if (authoritativeReplay) {
    try {
      await dependencies.authenticate(request, env);
      const input = await readAuthoritativeReplayRequest(
        request,
        env.REVIEWED_EXECUTION_PROFILE_DIGEST,
        env.REVIEWED_MEASUREMENT_CONFIG_DIGEST,
        env.REVIEWED_VM_IMAGE_DIGEST,
      );
      const sandbox = dependencies.sandbox(env, input.runner_nonce);
      const verdict = await withSandboxDestruction(sandbox, async () => {
          await writeSandboxFile(sandbox, "/workspace/replay-request.json", JSON.stringify(input.request));
          await writeSandboxFile(
            sandbox,
            "/workspace/archive-expectation.json",
            JSON.stringify(input.archive_expectation),
          );
          await writeSandboxFile(sandbox, "/workspace/archive.tar.gz.age.b64", input.ciphertext_base64);
          await writeSandboxFile(sandbox, "/workspace/identity.age.b64", input.plaintext_identity_base64);
          const stdout = await executeSandboxCommand(
            sandbox,
            "/opt/lean-eval/replay-authoritative",
            20_100_000,
            64 * 1024,
          );
          try {
            return validateReplayVerdict(JSON.parse(stdout) as unknown, input);
          } catch {
            throw new ReplayExecutorError("command_output_invalid");
          }
      });
      return json({ schema_version: 1, verdict, destruction: "confirmed" });
    } catch (error) {
      if (error instanceof ReplayAuthError) return json({ error: "unauthorized" }, 401);
      if (error instanceof AuthoritativeReplayContractError || error instanceof SyntaxError) {
        return json({ error: "invalid_request" }, 400);
      }
      recordExecutorFailure("authoritative_replay", error);
      return json({ error: "executor_failed" }, 500);
    }
  }
  if (env.DEPLOYMENT_ENVIRONMENT !== "staging" || env.STAGING_ACCEPTANCE_ENABLED !== "true") {
    return json({ error: "staging_acceptance_disabled" }, 503);
  }
  try {
    await dependencies.authenticate(request, env);
    if (archiveAcceptance) {
      const input = await readArchiveAcceptanceRequest(request);
      const sandbox = dependencies.sandbox(env, input.runner_nonce);
      const evidence = await withSandboxDestruction(sandbox, async () => {
          await writeSandboxFile(sandbox, "/workspace/archive.tar.gz.age.b64", input.ciphertext_base64);
          await writeSandboxFile(sandbox, "/workspace/identity.age.b64", input.plaintext_identity_base64);
          await writeSandboxFile(
            sandbox,
            "/workspace/archive-expectation.json",
            JSON.stringify({
              schema_version: 1,
              submission_id: input.submission_id,
              archive_ciphertext_sha256: input.archive_ciphertext_sha256,
              plaintext_tar_sha256: input.plaintext_tar_sha256,
              plaintext_tar_size: input.plaintext_tar_size,
            }),
          );
          const stdout = await executeSandboxCommand(
            sandbox,
            "/opt/lean-eval/replay-archive-acceptance",
            180_000,
            4096,
          );
          try {
            return validateArchiveEvidence(JSON.parse(stdout) as unknown, input);
          } catch {
            throw new ReplayExecutorError("command_output_invalid");
          }
      });
      return json({
        schema_version: 1,
        service: "lean-eval-replay-executor",
        environment: "staging",
        request_id: input.request_id,
        runner_nonce: input.runner_nonce,
        submission_id: evidence.submission_id,
        archive_ciphertext_sha256: evidence.archive_ciphertext_sha256,
        plaintext_tar_sha256: evidence.plaintext_tar_sha256,
        plaintext_tar_size: evidence.plaintext_tar_size,
        network_policy: "disabled",
        network_probe: evidence.network_probe,
        destruction: "confirmed",
        architecture: evidence.architecture,
        kernel_release: evidence.kernel_release,
        cpu_model: evidence.cpu_model,
        staging_memory_limit_bytes: Number(env.STAGING_MEMORY_LIMIT_BYTES),
        production_memory_gate_bytes: Number(env.PRODUCTION_MEMORY_GATE_BYTES),
      });
    }
    const input = await readAcceptanceRequest(request);
    const sandbox = dependencies.sandbox(env, input.runner_nonce);
    const evidence = await withSandboxDestruction(sandbox, async () => {
        await writeSandboxFile(sandbox, "/workspace/archive.tar.gz.age.b64", input.ciphertext_base64);
        await writeSandboxFile(sandbox, "/workspace/identity.age.b64", input.plaintext_identity_base64);
        await writeSandboxFile(
          sandbox,
          "/workspace/expectation.json",
          JSON.stringify({
            schema_version: 1,
            archive_ciphertext_sha256: input.archive_ciphertext_sha256,
            marker_sha256: input.marker_sha256,
          }),
        );
        const stdout = await executeSandboxCommand(
          sandbox,
          "/opt/lean-eval/replay-staging-acceptance",
          120_000,
          4096,
        );
        try {
          return validateSandboxEvidence(JSON.parse(stdout) as unknown, input);
        } catch {
          throw new ReplayExecutorError("command_output_invalid");
        }
    });
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
    if (
      error instanceof ReplayContractError ||
      error instanceof ReplayArchiveContractError ||
      error instanceof SyntaxError
    ) {
      return json({ error: "invalid_request" }, 400);
    }
    recordExecutorFailure(archiveAcceptance ? "archive_acceptance" : "synthetic_acceptance", error);
    return json({ error: "executor_failed" }, 500);
  }
}
