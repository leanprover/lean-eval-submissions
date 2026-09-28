import { DurableObject } from "cloudflare:workers";

import { replaySandbox, type ReplaySandboxEnvironment } from "./replay-sandbox";

const ACTIVE_BINDING_KEY = "authoritative-active-binding:v1";
const RECEIPT_KEY = "authoritative-terminal-receipt:v1";
const RESERVATION_KEY = "historical-cleanup-reservation:v1";
const CLEANUP_KEY = "authoritative-sandbox-cleanup:v1";
const ARCHIVE_UPLOAD_KEY = "authoritative-archive-upload:v1";
const CLEANUP_RETRY_MS = 5 * 60 * 1000;
// An archive upload precedes the key unwrap, so its Sandbox exists before any
// replay binding does. Without a deadline of its own an abandoned upload would
// hold the single permitted container until it idled out, with nothing durable
// recording that it should be destroyed.
const ARCHIVE_UPLOAD_LEASE_MS = 30 * 60 * 1000;
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

type ArchiveUploadIdentityRecord = {
  schema_version: 1;
  upload_kind: string;
  runner_nonce: string;
  archive_sha256: string;
  archive_bytes: number;
  part_count: number;
};

type ArchiveUploadPartRecord = {
  index: number;
  sha256: string;
  bytes: number;
  path: string;
};

type ArchiveUploadRecord = ArchiveUploadIdentityRecord & {
  expires_at_epoch_ms: number;
  parts: ArchiveUploadPartRecord[];
  assembled_path: string | null;
};

function archiveUploadIdentity(value: unknown): ArchiveUploadIdentityRecord {
  const stored = record(value, "durable archive upload");
  if (
    stored.schema_version !== 1
    || typeof stored.upload_kind !== "string"
    || stored.upload_kind.length === 0
    || typeof stored.runner_nonce !== "string"
    || !/^[0-9a-f]{64}$/.test(stored.runner_nonce)
    || typeof stored.archive_sha256 !== "string"
    || !/^[0-9a-f]{64}$/.test(stored.archive_sha256)
    || !Number.isSafeInteger(stored.archive_bytes)
    || (stored.archive_bytes as number) < 1
    || !Number.isSafeInteger(stored.part_count)
    || (stored.part_count as number) < 1
  ) {
    throw new Error("durable archive upload identity is invalid");
  }
  return {
    schema_version: 1,
    upload_kind: stored.upload_kind,
    runner_nonce: stored.runner_nonce,
    archive_sha256: stored.archive_sha256,
    archive_bytes: stored.archive_bytes as number,
    part_count: stored.part_count as number,
  };
}

function sameArchiveUpload(
  left: ArchiveUploadIdentityRecord,
  right: ArchiveUploadIdentityRecord,
): boolean {
  return left.upload_kind === right.upload_kind
    && left.runner_nonce === right.runner_nonce
    && left.archive_sha256 === right.archive_sha256
    && left.archive_bytes === right.archive_bytes
    && left.part_count === right.part_count;
}

function archiveUploadPart(value: unknown, partCount: number): ArchiveUploadPartRecord {
  const part = record(value, "durable archive upload part");
  if (
    !Number.isSafeInteger(part.index)
    || (part.index as number) < 0
    || (part.index as number) >= partCount
    || typeof part.sha256 !== "string"
    || !/^[0-9a-f]{64}$/.test(part.sha256)
    || !Number.isSafeInteger(part.bytes)
    || (part.bytes as number) < 1
    || typeof part.path !== "string"
    || part.path.length === 0
  ) {
    throw new Error("durable archive upload part is invalid");
  }
  return {
    index: part.index as number,
    sha256: part.sha256,
    bytes: part.bytes as number,
    path: part.path,
  };
}

function archiveUploadRecord(value: unknown): ArchiveUploadRecord {
  const stored = record(value, "durable archive upload");
  const identity = archiveUploadIdentity(stored);
  if (
    !Number.isSafeInteger(stored.expires_at_epoch_ms)
    || !Array.isArray(stored.parts)
    || stored.parts.length > identity.part_count
    || (stored.assembled_path !== null && typeof stored.assembled_path !== "string")
  ) {
    throw new Error("durable archive upload is invalid");
  }
  const parts = stored.parts.map((entry) => archiveUploadPart(entry, identity.part_count));
  if (new Set(parts.map((part) => part.index)).size !== parts.length) {
    throw new Error("durable archive upload part is invalid");
  }
  return {
    ...identity,
    expires_at_epoch_ms: stored.expires_at_epoch_ms as number,
    parts,
    assembled_path: stored.assembled_path,
  };
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
    return this.claimBindingWithReservation(
      binding,
      historicalBinding(binding),
      false,
    );
  }

  async claimReservedBinding(binding: unknown): Promise<unknown> {
    return this.claimBindingWithReservation(binding, true, true);
  }

  async readArchiveUpload(): Promise<unknown> {
    const upload = await this.ctx.storage.get(ARCHIVE_UPLOAD_KEY);
    return upload === undefined ? null : upload;
  }

  /**
   * First-writer-wins on the upload identity. An exact repeat returns the stored
   * record so a lost response can be retried; a different archive under the same
   * nonce is refused rather than blended into the first one.
   */
  async claimArchiveUpload(identity: unknown): Promise<unknown> {
    const wanted = archiveUploadIdentity(identity);
    const now = Date.now();
    return this.ctx.storage.transaction(async (transaction) => {
      // A nonce that already reached a terminal outcome is spent. Reusing it
      // would attach a fresh archive to a destroyed or destroying Sandbox.
      if (
        await transaction.get(CLEANUP_KEY) !== undefined
        || await transaction.get(RECEIPT_KEY) !== undefined
      ) {
        throw new Error("runner nonce has already been finalized");
      }
      const existing = await transaction.get(ARCHIVE_UPLOAD_KEY);
      if (existing !== undefined) {
        const stored = archiveUploadRecord(existing);
        if (!sameArchiveUpload(stored, wanted)) {
          throw new Error("runner nonce is already bound to a different archive upload");
        }
        return stored;
      }
      const claimed: ArchiveUploadRecord = {
        ...wanted,
        expires_at_epoch_ms: now + ARCHIVE_UPLOAD_LEASE_MS,
        parts: [],
        assembled_path: null,
      };
      await transaction.put(ARCHIVE_UPLOAD_KEY, claimed);
      // The upload owns the only alarm until a replay binding is claimed, so an
      // abandoned upload still has something durable that will destroy it.
      const alarm = await transaction.getAlarm();
      if (alarm === null || alarm > claimed.expires_at_epoch_ms) {
        await transaction.setAlarm(claimed.expires_at_epoch_ms);
      }
      return claimed;
    });
  }

  /**
   * Record one part at its committed Sandbox path. The path is chosen by the
   * caller per request and never reused, so a retry cannot overwrite the bytes
   * an earlier part already committed.
   */
  async commitArchiveUploadPart(identity: unknown, part: unknown): Promise<unknown> {
    const wanted = archiveUploadIdentity(identity);
    const committed = record(part, "archive upload part");
    return this.ctx.storage.transaction(async (transaction) => {
      const existing = await transaction.get(ARCHIVE_UPLOAD_KEY);
      if (existing === undefined) throw new Error("archive upload was not claimed");
      const stored = archiveUploadRecord(existing);
      if (!sameArchiveUpload(stored, wanted)) {
        throw new Error("archive upload identity mismatch");
      }
      if (stored.assembled_path !== null) {
        throw new Error("archive upload was already finalized");
      }
      const added = archiveUploadPart(committed, stored.part_count);
      const previous = stored.parts.find((entry) => entry.index === added.index);
      if (previous !== undefined) {
        // Same bytes twice is a retry and succeeds. Different bytes at the same
        // index is two different archives racing, and must not silently win.
        if (previous.sha256 !== added.sha256 || previous.bytes !== added.bytes) {
          throw new Error("archive upload part conflicts with a committed part");
        }
        return stored;
      }
      const updated: ArchiveUploadRecord = { ...stored, parts: [...stored.parts, added] };
      await transaction.put(ARCHIVE_UPLOAD_KEY, updated);
      return updated;
    });
  }

  /** Mark the upload assembled. Only a complete, digest-matched part set qualifies. */
  async finalizeArchiveUpload(identity: unknown, assembledPath: string): Promise<unknown> {
    const wanted = archiveUploadIdentity(identity);
    if (assembledPath.length === 0) throw new Error("assembled archive path is invalid");
    return this.ctx.storage.transaction(async (transaction) => {
      const existing = await transaction.get(ARCHIVE_UPLOAD_KEY);
      if (existing === undefined) throw new Error("archive upload was not claimed");
      const stored = archiveUploadRecord(existing);
      if (!sameArchiveUpload(stored, wanted)) {
        throw new Error("archive upload identity mismatch");
      }
      if (stored.parts.length !== stored.part_count) {
        throw new Error("archive upload is incomplete");
      }
      if (stored.assembled_path !== null) {
        if (stored.assembled_path !== assembledPath) {
          throw new Error("archive upload was already finalized at a different path");
        }
        return stored;
      }
      const updated: ArchiveUploadRecord = { ...stored, assembled_path: assembledPath };
      await transaction.put(ARCHIVE_UPLOAD_KEY, updated);
      return updated;
    });
  }

  private async claimBindingWithReservation(
    binding: unknown,
    reservationRequired: boolean,
    rejectFinalized: boolean,
  ): Promise<unknown> {
    const expiry = retainedUntil(binding);
    const cleanupDeadline = cleanupAfter(binding);
    if (cleanupDeadline >= expiry) throw new Error("durable replay cleanup window is invalid");
    return this.ctx.storage.transaction(async (transaction) => {
      if (
        rejectFinalized
        && (
          await transaction.get(CLEANUP_KEY) !== undefined
          || await transaction.get(RECEIPT_KEY) !== undefined
        )
      ) {
        throw new Error("historical private replay was already finalized");
      }
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
          await transaction.delete([
            ACTIVE_BINDING_KEY,
            RECEIPT_KEY,
            RESERVATION_KEY,
            ARCHIVE_UPLOAD_KEY,
          ]);
        });
      } else {
        await this.ctx.storage.setAlarm(marker.retained_until_epoch_ms);
      }
      return;
    }
    const binding = await this.ctx.storage.get(ACTIVE_BINDING_KEY);
    if (binding === undefined) {
      // An upload that never reached a replay start still created a Sandbox.
      // Nothing else will destroy it, so the upload lease does.
      const upload = await this.ctx.storage.get(ARCHIVE_UPLOAD_KEY);
      if (upload === undefined) return;
      const stored = archiveUploadRecord(upload);
      if (stored.expires_at_epoch_ms > Date.now()) {
        await this.ctx.storage.setAlarm(stored.expires_at_epoch_ms);
        return;
      }
      try {
        await destroySandboxWithTimeout(replaySandbox(this.env, stored.runner_nonce));
      } catch {
        console.error(JSON.stringify({
          event: "lean_eval_replay_sandbox_cleanup_retry",
          reason: "abandoned_archive_upload_destroy_failed",
        }));
        await this.ctx.storage.setAlarm(Date.now() + CLEANUP_RETRY_MS);
        return;
      }
      // No tombstone: an upload that never started has no State lifecycle to
      // reconcile, and the record is nonce-bearing so it should not be retained.
      await this.ctx.storage.delete(ARCHIVE_UPLOAD_KEY);
      return;
    }
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
