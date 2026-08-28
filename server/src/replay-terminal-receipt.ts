import { DurableObject } from "cloudflare:workers";

import { replaySandbox, type ReplaySandboxEnvironment } from "./replay-sandbox";

const ACTIVE_BINDING_KEY = "authoritative-active-binding:v1";
const RECEIPT_KEY = "authoritative-terminal-receipt:v1";
const RESERVATION_KEY = "historical-cleanup-reservation:v1";
const CLEANUP_KEY = "authoritative-sandbox-cleanup:v1";
const CLEANUP_RETRY_MS = 5 * 60 * 1000;
const SANDBOX_DESTROY_TIMEOUT_MS = 4 * 60 * 1000;
const CONFIRMATION_RETENTION_MS = 24 * 60 * 60 * 1000;

type CleanupIdentity = {
  schema_version: 1;
  replay_task_id: string;
  attempt: number;
};

type CleanupTombstone = CleanupIdentity & {
  destruction_state: "confirmed";
};

type CleanupConfirmation = CleanupTombstone & {
  confirmed_at_epoch_ms: number;
  retained_until_epoch_ms: number;
};

function record(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} is invalid`);
  }
  return value as Record<string, unknown>;
}

function safeInteger(value: unknown, label: string): number {
  if (!Number.isSafeInteger(value)) throw new Error(`${label} is invalid`);
  return value as number;
}

function retainedUntil(value: unknown): number {
  return safeInteger(record(value, "durable replay state").retained_until_epoch_ms, "durable replay retention");
}

function cleanupAfter(value: unknown): number {
  return safeInteger(record(value, "durable replay state").cleanup_after_epoch_ms, "durable replay cleanup deadline");
}

function cleanupIdentity(value: unknown): CleanupIdentity {
  const binding = record(value, "durable replay binding");
  if (
    binding.schema_version !== 1
    || typeof binding.replay_task_id !== "string"
    || !/^rt1_[0-9a-f]{64}$/.test(binding.replay_task_id)
    || !Number.isSafeInteger(binding.attempt)
    || (binding.attempt as number) < 1
  ) {
    throw new Error("durable replay cleanup identity is invalid");
  }
  return {
    schema_version: 1,
    replay_task_id: binding.replay_task_id,
    attempt: binding.attempt as number,
  };
}

function runnerNonce(value: unknown): string {
  const nonce = record(value, "durable replay binding").runner_nonce;
  if (typeof nonce !== "string" || !/^[0-9a-f]{64}$/.test(nonce)) {
    throw new Error("durable replay runner nonce is invalid");
  }
  return nonce;
}

function confirmedReceipt(value: unknown): unknown {
  return { ...record(value, "terminal receipt"), destruction_state: "confirmed" };
}

function destructionMarker(value: unknown): CleanupTombstone | CleanupConfirmation {
  const stored = record(value, "sandbox cleanup confirmation");
  const identity = cleanupIdentity(stored);
  if (stored.destruction_state !== "confirmed") {
    throw new Error("sandbox cleanup confirmation is invalid");
  }
  const tombstone: CleanupTombstone = {
    ...identity,
    destruction_state: "confirmed",
  };
  const hasConfirmedAt = Object.hasOwn(stored, "confirmed_at_epoch_ms");
  const hasRetainedUntil = Object.hasOwn(stored, "retained_until_epoch_ms");
  if (!hasConfirmedAt && !hasRetainedUntil) return tombstone;
  if (
    !hasConfirmedAt
    || !hasRetainedUntil
    || !Number.isSafeInteger(stored.confirmed_at_epoch_ms)
    || !Number.isSafeInteger(stored.retained_until_epoch_ms)
    || (stored.retained_until_epoch_ms as number) <= (stored.confirmed_at_epoch_ms as number)
  ) {
    throw new Error("sandbox cleanup confirmation is invalid");
  }
  return {
    ...tombstone,
    confirmed_at_epoch_ms: stored.confirmed_at_epoch_ms as number,
    retained_until_epoch_ms: stored.retained_until_epoch_ms as number,
  };
}

function sameIdentity(left: CleanupIdentity, right: CleanupIdentity): boolean {
  return left.replay_task_id === right.replay_task_id
    && left.attempt === right.attempt;
}

function historicalBinding(value: unknown): boolean {
  const binding = record(value, "durable replay binding");
  return Object.hasOwn(binding, "request_id") || Object.hasOwn(binding, "result_id");
}

async function destroySandboxWithTimeout(
  sandbox: { destroy(): Promise<void> },
): Promise<void> {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      sandbox.destroy(),
      new Promise<never>((_resolve, reject) => {
        timeout = setTimeout(
          () => reject(new Error("sandbox destruction timed out")),
          SANDBOX_DESTROY_TIMEOUT_MS,
        );
      }),
    ]);
  } finally {
    if (timeout !== undefined) clearTimeout(timeout);
  }
}

export class ReplayTerminalReceipt extends DurableObject<ReplaySandboxEnvironment> {
  async readBinding(): Promise<unknown> {
    const binding = await this.ctx.storage.get(ACTIVE_BINDING_KEY);
    return binding === undefined ? null : binding;
  }

  async claimBinding(binding: unknown): Promise<unknown> {
    return this.claimBindingWithReservation(binding, historicalBinding(binding));
  }

  async claimReservedBinding(binding: unknown): Promise<unknown> {
    return this.claimBindingWithReservation(binding, true);
  }

  private async claimBindingWithReservation(
    binding: unknown,
    reservationRequired: boolean,
  ): Promise<unknown> {
    const expiry = retainedUntil(binding);
    const cleanupDeadline = cleanupAfter(binding);
    if (cleanupDeadline >= expiry) throw new Error("durable replay cleanup window is invalid");
    return this.ctx.storage.transaction(async (transaction) => {
      const existing = await transaction.get(ACTIVE_BINDING_KEY);
      if (existing !== undefined) return existing;
      if (reservationRequired) {
        const reservation = await transaction.get(RESERVATION_KEY);
        if (
          reservation === undefined
          || !sameIdentity(cleanupIdentity(reservation), cleanupIdentity(binding))
        ) {
          throw new Error("historical cleanup identity was not reserved");
        }
      }
      await transaction.put(ACTIVE_BINDING_KEY, binding);
      await transaction.setAlarm(cleanupDeadline);
      return binding;
    });
  }

  async reserveCleanupIdentity(expected: CleanupIdentity): Promise<unknown> {
    const identity = cleanupIdentity(expected);
    return this.ctx.storage.transaction(async (transaction) => {
      const marker = await transaction.get(CLEANUP_KEY);
      if (marker !== undefined) {
        const confirmed = destructionMarker(marker);
        if (!sameIdentity(confirmed, identity)) {
          throw new Error("sandbox cleanup identity mismatch");
        }
        throw new Error("historical cleanup identity was already finalized");
      }
      const binding = await transaction.get(ACTIVE_BINDING_KEY);
      if (binding !== undefined && !sameIdentity(cleanupIdentity(binding), identity)) {
        throw new Error("sandbox cleanup identity mismatch");
      }
      const reserved = await transaction.get(RESERVATION_KEY);
      if (reserved !== undefined) {
        const existing = cleanupIdentity(reserved);
        if (!sameIdentity(existing, identity)) {
          throw new Error("sandbox cleanup identity mismatch");
        }
        return existing;
      }
      await transaction.put(RESERVATION_KEY, identity);
      return identity;
    });
  }

  async readReceipt(): Promise<unknown> {
    const receipt = await this.ctx.storage.get(RECEIPT_KEY);
    if (receipt === undefined) return null;
    if (retainedUntil(receipt) <= Date.now()) {
      await this.ctx.storage.delete(RECEIPT_KEY);
      return null;
    }
    return receipt;
  }

  async prepareReceipt(receipt: unknown): Promise<unknown> {
    retainedUntil(receipt);
    return this.ctx.storage.transaction(async (transaction) => {
      const existing = await transaction.get(RECEIPT_KEY);
      if (existing !== undefined) return existing;
      const binding = await transaction.get(ACTIVE_BINDING_KEY);
      if (binding === undefined) throw new Error("durable replay binding is unavailable");
      await transaction.put(RECEIPT_KEY, receipt);
      await transaction.setAlarm(cleanupAfter(binding));
      return receipt;
    });
  }

  async confirmReceipt(): Promise<unknown> {
    const now = Date.now();
    return this.ctx.storage.transaction(async (transaction) => {
      const existing = await transaction.get(RECEIPT_KEY);
      const binding = await transaction.get(ACTIVE_BINDING_KEY);
      if (existing === undefined) throw new Error("terminal receipt is unavailable");
      if (binding === undefined) throw new Error("durable replay binding is unavailable");
      const alreadyConfirmed = record(existing, "terminal receipt").destruction_state === "confirmed";
      const confirmed = alreadyConfirmed ? existing : confirmedReceipt(existing);
      const marker: CleanupConfirmation = {
        ...cleanupIdentity(binding),
        destruction_state: "confirmed",
        confirmed_at_epoch_ms: now,
        retained_until_epoch_ms: now + CONFIRMATION_RETENTION_MS,
      };
      await transaction.put(RECEIPT_KEY, confirmed);
      await transaction.put(CLEANUP_KEY, marker);
      await transaction.delete(RESERVATION_KEY);
      await transaction.setAlarm(marker.retained_until_epoch_ms);
      return confirmed;
    });
  }

  async destroyBoundSandbox(expected: CleanupIdentity): Promise<unknown> {
    const storedMarker = await this.ctx.storage.get(CLEANUP_KEY);
    if (storedMarker !== undefined) {
      const marker = destructionMarker(storedMarker);
      if (!sameIdentity(marker, expected)) throw new Error("sandbox cleanup identity mismatch");
      return marker;
    }
    const binding = await this.ctx.storage.get(ACTIVE_BINDING_KEY);
    if (binding === undefined) {
      const reserved = await this.ctx.storage.get(RESERVATION_KEY);
      if (reserved === undefined) return null;
      const identity = cleanupIdentity(reserved);
      if (!sameIdentity(identity, expected)) {
        throw new Error("sandbox cleanup identity mismatch");
      }
      await this.confirmAbsentSandbox(identity);
      const confirmed = await this.ctx.storage.get(CLEANUP_KEY);
      if (confirmed === undefined) throw new Error("sandbox cleanup confirmation is unavailable");
      return destructionMarker(confirmed);
    }
    const identity = cleanupIdentity(binding);
    if (!sameIdentity(identity, expected)) throw new Error("sandbox cleanup identity mismatch");
    try {
      await this.destroyAndConfirm(binding);
    } catch (error) {
      await this.ctx.storage.setAlarm(Date.now() + CLEANUP_RETRY_MS);
      throw error;
    }
    const confirmed = await this.ctx.storage.get(CLEANUP_KEY);
    if (confirmed === undefined) throw new Error("sandbox cleanup confirmation is unavailable");
    return destructionMarker(confirmed);
  }

  private async destroyAndConfirm(binding: unknown): Promise<void> {
    await destroySandboxWithTimeout(replaySandbox(this.env, runnerNonce(binding)));
    const now = Date.now();
    await this.ctx.storage.transaction(async (transaction) => {
      const current = await transaction.get(ACTIVE_BINDING_KEY);
      if (current === undefined || runnerNonce(current) !== runnerNonce(binding)) {
        throw new Error("durable replay binding changed during cleanup");
      }
      const marker: CleanupConfirmation = {
        ...cleanupIdentity(current),
        destruction_state: "confirmed",
        confirmed_at_epoch_ms: now,
        retained_until_epoch_ms: now + CONFIRMATION_RETENTION_MS,
      };
      const receipt = await transaction.get(RECEIPT_KEY);
      if (receipt !== undefined) await transaction.put(RECEIPT_KEY, confirmedReceipt(receipt));
      await transaction.put(CLEANUP_KEY, marker);
      await transaction.delete(RESERVATION_KEY);
      await transaction.setAlarm(marker.retained_until_epoch_ms);
    });
  }

  private async confirmAbsentSandbox(identity: CleanupIdentity): Promise<void> {
    const now = Date.now();
    await this.ctx.storage.transaction(async (transaction) => {
      const binding = await transaction.get(ACTIVE_BINDING_KEY);
      if (binding !== undefined) throw new Error("durable replay binding appeared during cleanup");
      const reserved = await transaction.get(RESERVATION_KEY);
      if (
        reserved === undefined
        || !sameIdentity(cleanupIdentity(reserved), identity)
      ) {
        throw new Error("sandbox cleanup identity mismatch");
      }
      const marker: CleanupConfirmation = {
        ...identity,
        destruction_state: "confirmed",
        confirmed_at_epoch_ms: now,
        retained_until_epoch_ms: now + CONFIRMATION_RETENTION_MS,
      };
      await transaction.put(CLEANUP_KEY, marker);
      await transaction.delete(RESERVATION_KEY);
      await transaction.setAlarm(marker.retained_until_epoch_ms);
    });
  }

  override async alarm(): Promise<void> {
    const storedMarker = await this.ctx.storage.get(CLEANUP_KEY);
    if (storedMarker !== undefined) {
      const marker = destructionMarker(storedMarker);
      if (
        !("retained_until_epoch_ms" in marker)
        || marker.retained_until_epoch_ms <= Date.now()
      ) {
        // Keep the exact, source-free destruction tombstone indefinitely. State
        // recovery may be invoked manually long after nonce-bearing state is purged.
        await this.ctx.storage.transaction(async (transaction) => {
          await transaction.put(CLEANUP_KEY, {
            schema_version: marker.schema_version,
            replay_task_id: marker.replay_task_id,
            attempt: marker.attempt,
            destruction_state: marker.destruction_state,
          } satisfies CleanupTombstone);
          await transaction.delete([ACTIVE_BINDING_KEY, RECEIPT_KEY, RESERVATION_KEY]);
        });
      } else {
        await this.ctx.storage.setAlarm(marker.retained_until_epoch_ms);
      }
      return;
    }
    const binding = await this.ctx.storage.get(ACTIVE_BINDING_KEY);
    if (binding === undefined) return;
    try {
      await this.destroyAndConfirm(binding);
    } catch {
      console.error(JSON.stringify({
        event: "lean_eval_replay_sandbox_cleanup_retry",
        reason: "sandbox_destroy_failed",
      }));
      await this.ctx.storage.setAlarm(Date.now() + CLEANUP_RETRY_MS);
    }
  }
}
