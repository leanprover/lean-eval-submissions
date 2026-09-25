/**
 * Temporary one-task entry point for historical private replay.
 *
 * Delete this entry, its workflow, and the controller-only rendering support
 * after the historical private queue is empty and no replay is running.
 */
import { ContainerProxy, Sandbox } from "@cloudflare/sandbox";

import { handleReplayRequest, type ReplayRuntimeEnv } from "./replay-app";
import { ReplayTerminalReceipt } from "./replay-terminal-receipt";
import {
  historicalReceiptObjectName,
  replaySandbox,
} from "./replay-sandbox";

export { ContainerProxy, ReplayTerminalReceipt };

export class ReplaySandbox extends Sandbox {
  override enableInternet = false;
}

const REPLAY_ID = /^rt1_[0-9a-f]{64}$/;
const ALLOWED_ROUTES = new Set([
  "GET /healthz",
  "POST /api/v1/replay",
  "POST /api/v1/replay/status",
  "POST /api/v1/historical-private-replay/prewarm",
  "POST /api/v1/historical-private-replay/reserve",
  "POST /api/v1/historical-private-replay/cleanup",
]);

type CleanupIdentity = {
  schema_version: 1;
  replay_task_id: string;
  attempt: number;
};

function exactIdentity(value: unknown, env: ReplayRuntimeEnv): CleanupIdentity {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("cleanup identity is invalid");
  }
  const identity = value as Record<string, unknown>;
  if (
    Object.keys(identity).sort().join(",") !== "attempt,replay_task_id,schema_version"
    || identity.schema_version !== 1
    || typeof identity.replay_task_id !== "string"
    || !REPLAY_ID.test(identity.replay_task_id)
    || !Number.isSafeInteger(identity.attempt)
    || (identity.attempt as number) < 1
    || (identity.attempt as number) > 4
    || identity.replay_task_id !== env.EXPECTED_REPLAY_TASK_ID
    || String(identity.attempt) !== env.EXPECTED_REPLAY_ATTEMPT
  ) {
    throw new Error("cleanup identity differs from the exact task");
  }
  return {
    schema_version: 1,
    replay_task_id: identity.replay_task_id,
    attempt: identity.attempt as number,
  };
}

function exactMarker(
  value: unknown,
  identity: CleanupIdentity,
): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("cleanup marker is invalid");
  }
  const marker = value as Record<string, unknown>;
  if (
    marker.schema_version !== 1
    || marker.replay_task_id !== identity.replay_task_id
    || marker.attempt !== identity.attempt
  ) {
    throw new Error("cleanup marker differs from the exact task");
  }
  return marker;
}

function response(value: unknown, status = 200): Response {
  return Response.json(value, {
    status,
    headers: { "cache-control": "no-store" },
  });
}

export default {
  async fetch(request, env): Promise<Response> {
    const key = `${request.method} ${new URL(request.url).pathname}`;
    if (!ALLOWED_ROUTES.has(key)) return response({ error: "not_found" }, 404);
    if (
      key === "POST /api/v1/historical-private-replay/reserve"
      || key === "POST /api/v1/historical-private-replay/cleanup"
    ) {
      try {
        const { verifyGithubOidc } = await import("./replay-auth");
        await verifyGithubOidc(request, env);
        const identity = exactIdentity(await request.json(), env);
        const store = env.REPLAY_TERMINAL_RECEIPT.getByName(
          historicalReceiptObjectName(identity.replay_task_id, identity.attempt),
        );
        if (key.endsWith("/reserve")) {
          exactMarker(
            await Promise.resolve(store.reserveCleanupIdentity(identity)),
            identity,
          );
          return response({ ...identity, status: "reserved" });
        }
        const marker = exactMarker(
          await Promise.resolve(store.destroyBoundSandbox(identity)),
          identity,
        );
        if (marker.destruction_state !== "confirmed") {
          throw new Error("sandbox destruction was not confirmed");
        }
        return response({ ...identity, destruction: "confirmed" });
      } catch {
        return response({ error: "cleanup_failed" }, 500);
      }
    }
    return handleReplayRequest(request, env, {
      authenticate: async (incoming, runtime) => {
        const { verifyGithubOidc } = await import("./replay-auth");
        await verifyGithubOidc(incoming, runtime);
      },
      async sandbox(runtime, runnerNonce) {
        // The large replay image can need more than three minutes to become
        // port-ready after a cold regional pull. getSandbox applies options in
        // the background, so explicitly await the same configuration before
        // the first RPC; otherwise that RPC can retain the ordinary 330-second
        // transport budget. This does not change ordinary replay.
        // State CAS and one-use KMS preparation took just over four minutes in
        // the bounded production canary. Keep this exact reserved sandbox
        // awake with enough margin for the source-free readiness refresh;
        // every terminal and failure path still destroys it explicitly.
        const sandbox = replaySandbox(runtime, runnerNonce, 600_000, "15m");
        await sandbox.configure({
          keepAlive: false,
          sleepAfter: "15m",
          containerTimeouts: {
            instanceGetTimeoutMS: 120_000,
            portReadyTimeoutMS: 600_000,
          },
        });
        return sandbox;
      },
      receiptStore(runtime) {
        const replayTaskId = runtime.EXPECTED_REPLAY_TASK_ID;
        const attempt = Number(runtime.EXPECTED_REPLAY_ATTEMPT);
        if (replayTaskId === undefined || !REPLAY_ID.test(replayTaskId)) {
          throw new Error("exact replay task is unavailable");
        }
        const store = runtime.REPLAY_TERMINAL_RECEIPT.getByName(
          historicalReceiptObjectName(replayTaskId, attempt),
        );
        return {
          claimBinding: (binding: unknown) => store.claimReservedBinding(binding),
          readBinding: () => store.readBinding(),
          readReceipt: () => store.readReceipt(),
          prepareReceipt: (receipt: unknown) => store.prepareReceipt(receipt),
          confirmReceipt: () => store.confirmReceipt(),
        };
      },
    });
  },
} satisfies ExportedHandler<ReplayRuntimeEnv>;
