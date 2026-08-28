import { getSandbox, type Sandbox } from "@cloudflare/sandbox";

export type ReplaySandboxEnvironment = {
  REPLAY_SANDBOX: DurableObjectNamespace<Sandbox>;
};

export function replaySandboxId(runnerNonce: string): string {
  return `r-${runnerNonce.slice(0, 61)}`;
}

export function replaySandbox(
  env: ReplaySandboxEnvironment,
  runnerNonce: string,
): Sandbox {
  return getSandbox(env.REPLAY_SANDBOX, replaySandboxId(runnerNonce), {
    enableDefaultSession: false,
    keepAlive: false,
    sleepAfter: "5m",
    containerTimeouts: {
      instanceGetTimeoutMS: 120_000,
      portReadyTimeoutMS: 180_000,
    },
  });
}

export function historicalReceiptObjectName(
  replayTaskId: string,
  attempt: number,
): string {
  const match = /^rt1_([0-9a-f]{64})$/.exec(replayTaskId);
  if (match?.[1] === undefined || !Number.isSafeInteger(attempt) || attempt < 1 || attempt > 4) {
    throw new Error("historical cleanup identity is invalid");
  }
  // State permits four total attempts (the initial execution and at most three
  // retries). Keeping 224 digest bits makes this
  // DNS-label-safe without making cleanup depend on the cancelled runner nonce.
  return `h-${match[1].slice(0, 56)}-${String(attempt)}`;
}
