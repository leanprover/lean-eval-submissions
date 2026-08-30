/**
 * Temporary, disposable entry point for qualifying one historical private image.
 *
 * This source is intentionally isolated from replay-entry.ts: its rendered
 * Wrangler configuration has a unique per-run Worker name, one immutable image
 * digest, one GitHub run/attempt, and one runner nonce. Delete this file and the
 * corresponding workflow/controller support after the 63 profiles are qualified.
 */
import { ContainerProxy, Sandbox } from "@cloudflare/sandbox";

import { handleReplayRequest, type ReplayRuntimeEnv } from "./replay-app";
import { readAuthoritativeReplayStatusRequest } from "./authoritative-replay-contract";
import { ReplayTerminalReceipt } from "./replay-terminal-receipt";
import { replaySandbox } from "./replay-sandbox";

export { ContainerProxy, ReplayTerminalReceipt };

export class PrivateQualificationSandbox extends Sandbox {
  override enableInternet = false;
}

const ALLOWED_ROUTES = new Set([
  "GET /healthz",
  "POST /api/v1/replay",
  "POST /api/v1/replay/status",
  "POST /api/v1/private-qualification/cleanup",
  "POST /api/v1/private-qualification/reserve",
]);

function cleanupResponse(value: unknown, expectedTaskId: string, expectedAttempt: number): Response {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("sandbox cleanup confirmation is invalid");
  }
  const marker = value as Record<string, unknown>;
  if (
    marker.schema_version !== 1
    || marker.replay_task_id !== expectedTaskId
    || marker.attempt !== expectedAttempt
    || marker.destruction_state !== "confirmed"
  ) {
    throw new Error("sandbox cleanup confirmation is invalid");
  }
  return Response.json({
    schema_version: 1,
    replay_task_id: expectedTaskId,
    attempt: expectedAttempt,
    destruction: "confirmed",
  }, { headers: { "cache-control": "no-store" } });
}

async function sha256Hex(value: ArrayBuffer): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", value));
  return [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export default {
  async fetch(request, env): Promise<Response> {
    const key = `${request.method} ${new URL(request.url).pathname}`;
    if (!ALLOWED_ROUTES.has(key)) {
      return Response.json(
        { error: "not_found" },
        { status: 404, headers: { "cache-control": "no-store" } },
      );
    }
    if (
      key === "POST /api/v1/private-qualification/cleanup"
      || key === "POST /api/v1/private-qualification/reserve"
    ) {
      try {
        const { verifyPrivateQualificationGithubOidc } = await import("./replay-auth");
        await verifyPrivateQualificationGithubOidc(request, env);
        const input = await readAuthoritativeReplayStatusRequest(
          request,
          env.REVIEWED_EXECUTION_PROFILE_DIGEST,
          env.REVIEWED_MEASUREMENT_CONFIG_DIGEST,
          env.REVIEWED_VM_IMAGE_DIGEST,
        );
        if (
          input.runner_nonce !== env.EXPECTED_RUNNER_NONCE
          || input.replay_task_id !== env.EXPECTED_REPLAY_TASK_ID
          || String(input.attempt) !== env.EXPECTED_REPLAY_ATTEMPT
        ) {
          return Response.json({ error: "invalid_request" }, { status: 400 });
        }
        const store = env.REPLAY_TERMINAL_RECEIPT.getByName(
          `q-${input.runner_nonce.slice(0, 61)}`,
        );
        if (key === "POST /api/v1/private-qualification/reserve") {
          const reserved: unknown = await Promise.resolve(store.reserveCleanupIdentity(input));
          if (
            typeof reserved !== "object"
            || reserved === null
            || Array.isArray(reserved)
            || (reserved as Record<string, unknown>).replay_task_id !== input.replay_task_id
            || (reserved as Record<string, unknown>).attempt !== input.attempt
          ) {
            throw new Error("cleanup reservation is invalid");
          }
          return Response.json({
            schema_version: 1,
            replay_task_id: input.replay_task_id,
            attempt: input.attempt,
            status: "reserved",
          }, { headers: { "cache-control": "no-store" } });
        }
        return cleanupResponse(
          await Promise.resolve(store.destroyBoundSandbox(input)),
          input.replay_task_id,
          input.attempt,
        );
      } catch {
        return Response.json(
          { error: "cleanup_failed" },
          { status: 500, headers: { "cache-control": "no-store" } },
        );
      }
    }
    if (key === "POST /api/v1/replay") {
      const digest = await sha256Hex(await request.clone().arrayBuffer());
      if (digest !== env.EXPECTED_QUALIFICATION_REQUEST_SHA256) {
        return Response.json(
          { error: "invalid_request" },
          { status: 400, headers: { "cache-control": "no-store" } },
        );
      }
    }
    return handleReplayRequest(request, env, {
      authenticate: async (incoming, runtime) => {
        const { verifyPrivateQualificationGithubOidc } = await import("./replay-auth");
        await verifyPrivateQualificationGithubOidc(incoming, runtime);
      },
      sandbox(runtime, runnerNonce) {
        return replaySandbox(runtime, runnerNonce);
      },
      receiptStore(runtime, runnerNonce) {
        return runtime.REPLAY_TERMINAL_RECEIPT.getByName(`q-${runnerNonce.slice(0, 61)}`);
      },
    });
  },
} satisfies ExportedHandler<ReplayRuntimeEnv>;
