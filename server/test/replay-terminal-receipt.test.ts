import { describe, expect, it, vi } from "vitest";

import { ReplayTerminalReceipt } from "../src/replay-terminal-receipt";

const ACTIVE_BINDING_KEY = "authoritative-active-binding:v1";
const RESERVATION_KEY = "historical-cleanup-reservation:v1";
const CLEANUP_KEY = "authoritative-sandbox-cleanup:v1";

type StorageFixture = {
  values: Map<string, unknown>;
  alarms: number[];
  get(key: string): Promise<unknown>;
  put(key: string, value: unknown): Promise<void>;
  delete(key: string | string[]): Promise<void>;
  setAlarm(value: number): Promise<void>;
  getAlarm(): Promise<number | null>;
  transaction<T>(callback: (transaction: StorageFixture) => Promise<T>): Promise<T>;
};

function storageFixture(): StorageFixture {
  const values = new Map<string, unknown>();
  const alarms: number[] = [];
  const storage: StorageFixture = {
    values,
    alarms,
    get: (key) => Promise.resolve(values.get(key)),
    put: (key, value) => {
      values.set(key, value);
      return Promise.resolve();
    },
    delete: (key) => {
      for (const item of Array.isArray(key) ? key : [key]) values.delete(item);
      return Promise.resolve();
    },
    getAlarm: () => Promise.resolve(alarms.at(-1) ?? null),
    setAlarm: (value) => {
      alarms.push(value);
      return Promise.resolve();
    },
    transaction: (callback) => callback(storage),
  };
  return storage;
}

function receiptFixture(storage: StorageFixture, destroy: () => Promise<void>): ReplayTerminalReceipt {
  const instance = Object.create(ReplayTerminalReceipt.prototype) as ReplayTerminalReceipt;
  const stub = {
    configure: () => Promise.resolve(),
    destroy,
    getProcess: () => Promise.reject(new Error("cleanup must not inspect process state")),
  };
  const namespace = {
    idFromName: (name: string) => name,
    get: () => stub,
  };
  Object.defineProperties(instance, {
    ctx: { value: { storage } },
    env: { value: { REPLAY_SANDBOX: namespace } },
  });
  return instance;
}

function activeBinding(): Record<string, unknown> {
  const now = Date.now();
  return {
    schema_version: 1,
    runner_nonce: "1".repeat(64),
    replay_task_id: `rt1_${"2".repeat(64)}`,
    attempt: 1,
    execution_profile_digest: "3".repeat(64),
    measurement_config_digest: "4".repeat(64),
    vm_image_digest: `sha256:${"5".repeat(64)}`,
    cleanup_after_epoch_ms: now + 7 * 60 * 60 * 1000,
    retained_until_epoch_ms: now + 24 * 60 * 60 * 1000,
  };
}

describe("durable replay sandbox cleanup", () => {
  it("can require a reservation for an authoritative private binding", async () => {
    const storage = storageFixture();
    const instance = receiptFixture(storage, () => Promise.resolve());
    const binding = activeBinding();
    const identity = {
      schema_version: 1 as const,
      replay_task_id: binding.replay_task_id as string,
      attempt: binding.attempt as number,
    };

    await expect(instance.claimReservedBinding(binding)).rejects.toThrow("was not reserved");
    expect(storage.values.has(ACTIVE_BINDING_KEY)).toBe(false);
    await instance.reserveCleanupIdentity(identity);
    expect(await instance.claimReservedBinding(binding)).toEqual(binding);
    expect(storage.values.get(ACTIVE_BINDING_KEY)).toEqual(binding);
  });

  it("destroys a prewarmed reserved sandbox even when replay never starts", async () => {
    const storage = storageFixture();
    let destroyCalls = 0;
    const instance = receiptFixture(storage, () => {
      destroyCalls += 1;
      return Promise.resolve();
    });
    const binding = activeBinding();
    const identity = {
      schema_version: 1 as const,
      replay_task_id: binding.replay_task_id as string,
      attempt: binding.attempt as number,
    };

    await instance.reserveCleanupIdentity(identity);
    await instance.claimReservedBinding(binding);
    const marker = await instance.destroyBoundSandbox(identity);

    expect(marker).toMatchObject({ ...identity, destruction_state: "confirmed" });
    expect(destroyCalls).toBe(1);
    expect(storage.values.get(ACTIVE_BINDING_KEY)).toEqual(binding);
    expect(storage.values.has(RESERVATION_KEY)).toBe(false);
  });

  it("rejects a reserved authoritative claim after cleanup", async () => {
    const storage = storageFixture();
    const instance = receiptFixture(storage, () => Promise.resolve());
    const binding = activeBinding();
    const identity = {
      schema_version: 1 as const,
      replay_task_id: binding.replay_task_id as string,
      attempt: binding.attempt as number,
    };

    await instance.reserveCleanupIdentity(identity);
    await instance.destroyBoundSandbox(identity);
    await expect(instance.claimReservedBinding(binding)).rejects.toThrow(
      "already finalized",
    );
    expect(storage.values.has(ACTIVE_BINDING_KEY)).toBe(false);
  });

  it("requires and atomically binds an exact historical cleanup reservation", async () => {
    const storage = storageFixture();
    const instance = receiptFixture(storage, () => Promise.resolve());
    const binding: Record<string, unknown> = {
      ...activeBinding(),
      request_id: `prr_${"3".repeat(64)}`,
      result_id: `r2_${"4".repeat(64)}`,
    };
    const identity = {
      schema_version: 1 as const,
      replay_task_id: binding.replay_task_id as string,
      attempt: binding.attempt as number,
    };

    await expect(instance.claimBinding(binding)).rejects.toThrow("was not reserved");
    expect(storage.values.has(ACTIVE_BINDING_KEY)).toBe(false);
    expect(await instance.reserveCleanupIdentity(identity)).toEqual(identity);
    expect(await instance.reserveCleanupIdentity(identity)).toEqual(identity);
    expect(await instance.claimBinding(binding)).toEqual(binding);
    expect(storage.values.get(ACTIVE_BINDING_KEY)).toEqual(binding);
  });

  it("confirms a pre-binding cancellation without sandbox lookup and keeps its tombstone", async () => {
    const storage = storageFixture();
    let destroyCalls = 0;
    const instance = receiptFixture(storage, () => {
      destroyCalls += 1;
      return Promise.resolve();
    });
    const binding = activeBinding();
    const identity = {
      schema_version: 1 as const,
      replay_task_id: binding.replay_task_id as string,
      attempt: binding.attempt as number,
    };

    await instance.reserveCleanupIdentity(identity);
    const first = await instance.destroyBoundSandbox(identity);
    const marker = storage.values.get(CLEANUP_KEY) as Record<string, unknown>;
    marker.confirmed_at_epoch_ms = 0;
    marker.retained_until_epoch_ms = 1;
    await instance.alarm();
    const repeated = await instance.destroyBoundSandbox(identity);

    expect(first).toMatchObject({ ...identity, destruction_state: "confirmed" });
    expect(repeated).toEqual({ ...identity, destruction_state: "confirmed" });
    expect(destroyCalls).toBe(0);
    expect(storage.values.has(RESERVATION_KEY)).toBe(false);
    expect(storage.values.has(CLEANUP_KEY)).toBe(true);
    await expect(instance.claimBinding({
      ...binding,
      request_id: `prr_${"3".repeat(64)}`,
      result_id: `r2_${"4".repeat(64)}`,
    })).rejects.toThrow("was not reserved");
    expect(storage.values.has(ACTIVE_BINDING_KEY)).toBe(false);
  });

  it("retains identity and explicitly rearms after alarm destruction failures", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      const storage = storageFixture();
      const binding = activeBinding();
      storage.values.set(ACTIVE_BINDING_KEY, binding);
      let destroyCalls = 0;
      const instance = receiptFixture(storage, () => {
        destroyCalls += 1;
        return destroyCalls <= 2
          ? Promise.reject(new Error("transient destroy failure"))
          : Promise.resolve();
      });

      await instance.alarm();
      await instance.alarm();

      expect(destroyCalls).toBe(2);
      expect(storage.values.get(ACTIVE_BINDING_KEY)).toEqual(binding);
      expect(storage.values.has(CLEANUP_KEY)).toBe(false);
      expect(storage.alarms).toHaveLength(2);
      expect(storage.alarms[0]).toBeGreaterThan(Date.now());
      expect(logged).toHaveBeenCalledTimes(2);
      expect(logged).toHaveBeenCalledWith(expect.stringContaining(
        "lean_eval_replay_sandbox_cleanup_retry",
      ));
      await instance.alarm();
      expect(destroyCalls).toBe(3);
      expect(storage.values.get(CLEANUP_KEY)).toMatchObject({
        replay_task_id: binding.replay_task_id,
        attempt: binding.attempt,
        destruction_state: "confirmed",
      });
    } finally {
      logged.mockRestore();
    }
  });

  it("bounds a hung alarm destruction and reaches explicit rearm", async () => {
    vi.useFakeTimers();
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      const storage = storageFixture();
      storage.values.set(ACTIVE_BINDING_KEY, activeBinding());
      const instance = receiptFixture(storage, () => new Promise<void>(() => undefined));

      const alarm = instance.alarm();
      await vi.advanceTimersByTimeAsync(4 * 60 * 1000);
      await alarm;

      expect(storage.values.has(CLEANUP_KEY)).toBe(false);
      expect(storage.alarms).toHaveLength(1);
      expect(storage.alarms[0]).toBeGreaterThan(Date.now());
      expect(logged).toHaveBeenCalledWith(expect.stringContaining("sandbox_destroy_failed"));
    } finally {
      logged.mockRestore();
      vi.useRealTimers();
    }
  });

  it("explicitly rearms when requested cleanup destruction fails", async () => {
    const storage = storageFixture();
    const binding = activeBinding();
    storage.values.set(ACTIVE_BINDING_KEY, binding);
    const instance = receiptFixture(
      storage,
      () => Promise.reject(new Error("transient destroy failure")),
    );

    await expect(instance.destroyBoundSandbox({
      schema_version: 1,
      replay_task_id: binding.replay_task_id as string,
      attempt: binding.attempt as number,
    })).rejects.toThrow("transient destroy failure");

    expect(storage.values.has(CLEANUP_KEY)).toBe(false);
    expect(storage.values.get(ACTIVE_BINDING_KEY)).toEqual(binding);
    expect(storage.alarms).toHaveLength(1);
    expect(storage.alarms[0]).toBeGreaterThan(Date.now());
  });

  it("destroys a running or sleeping exact sandbox and replays confirmation idempotently", async () => {
    const storage = storageFixture();
    const binding = activeBinding();
    storage.values.set(ACTIVE_BINDING_KEY, binding);
    let destroyCalls = 0;
    const instance = receiptFixture(storage, () => {
      destroyCalls += 1;
      return Promise.resolve();
    });
    const identity = {
      schema_version: 1 as const,
      replay_task_id: binding.replay_task_id as string,
      attempt: binding.attempt as number,
    };

    const first = await instance.destroyBoundSandbox(identity);
    const repeated = await instance.destroyBoundSandbox(identity);

    expect(first).toEqual(repeated);
    expect(first).toMatchObject({ ...identity, destruction_state: "confirmed" });
    expect(destroyCalls).toBe(1);
    expect(storage.values.get(ACTIVE_BINDING_KEY)).toEqual(binding);
    expect(storage.alarms).toHaveLength(1);

    const marker = storage.values.get(CLEANUP_KEY) as Record<string, unknown>;
    const expiredMarker = {
      ...marker,
      confirmed_at_epoch_ms: 0,
      retained_until_epoch_ms: 1,
    };
    storage.values.set(CLEANUP_KEY, expiredMarker);
    await instance.alarm();

    expect(storage.values.has(ACTIVE_BINDING_KEY)).toBe(false);
    expect(await instance.destroyBoundSandbox(identity)).toEqual({
      ...identity,
      destruction_state: "confirmed",
    });
    expect(destroyCalls).toBe(1);
  });

  it("rejects identity mismatch before touching the bound sandbox", async () => {
    const storage = storageFixture();
    const binding = activeBinding();
    storage.values.set(ACTIVE_BINDING_KEY, binding);
    let destroyCalls = 0;
    const instance = receiptFixture(storage, () => {
      destroyCalls += 1;
      return Promise.resolve();
    });

    await expect(instance.destroyBoundSandbox({
      schema_version: 1,
      replay_task_id: binding.replay_task_id as string,
      attempt: 2,
    })).rejects.toThrow("identity mismatch");
    expect(destroyCalls).toBe(0);
    expect(storage.values.get(ACTIVE_BINDING_KEY)).toEqual(binding);
  });

  it("purges nonce-bearing state but keeps a confirmed cleanup tombstone indefinitely", async () => {
    const storage = storageFixture();
    const binding = activeBinding();
    storage.values.set(ACTIVE_BINDING_KEY, binding);
    storage.values.set(CLEANUP_KEY, {
      schema_version: 1,
      replay_task_id: binding.replay_task_id,
      attempt: binding.attempt,
      destruction_state: "confirmed",
      confirmed_at_epoch_ms: 1,
      retained_until_epoch_ms: 2,
    });
    let destroyCalls = 0;
    const instance = receiptFixture(storage, () => {
      destroyCalls += 1;
      return Promise.resolve();
    });

    await instance.alarm();

    expect(storage.values.has(ACTIVE_BINDING_KEY)).toBe(false);
    expect(storage.values.has(CLEANUP_KEY)).toBe(true);
    expect(storage.values.size).toBe(1);
    expect(storage.values.get(CLEANUP_KEY)).toEqual({
      schema_version: 1,
      replay_task_id: binding.replay_task_id,
      attempt: binding.attempt,
      destruction_state: "confirmed",
    });
    expect(destroyCalls).toBe(0);
  });
});

const PRIVATE_CLEANUP_KEY = "historical-private-cleanup:v1";

describe("private pending cleanup barrier and fixed final sweep", () => {
  async function admitted(destroy: () => Promise<void>) {
    const storage = storageFixture();
    const instance = receiptFixture(storage, destroy);
    const binding = activeBinding();
    const identity = {
      schema_version: 1 as const,
      replay_task_id: binding.replay_task_id as string,
      attempt: binding.attempt as number,
    };
    await instance.reserveCleanupIdentity(identity);
    await instance.claimReservedBinding(binding);
    return { storage, instance, binding, identity };
  }

  it("closes admission before delayed C1 destroy ACK, then reaps late C2 at the original deadline", async () => {
    let acknowledge: (() => void) | undefined;
    let physicallyDestroyed: (() => void) | undefined;
    const entered = new Promise<void>((resolve) => { physicallyDestroyed = resolve; });
    let alive = true;
    // SDK destroy does not delete its stored idle policy. Model an old RPC
    // restarting the same ID while the first physical destroy ACK is delayed.
    // Persisted SDK configuration survives destroy and a new instance reads it.
    const sdkStorage = new Map<string, number>([["sleepAfter", 21600]]);
    let calls = 0;
    const { storage, instance, binding, identity } = await admitted(() => {
      calls += 1;
      alive = false;
      if (calls > 1) return Promise.resolve();
      physicallyDestroyed?.();
      return new Promise<void>((resolve) => { acknowledge = resolve; });
    });
    const cleanup = instance.destroyPrivateBoundSandbox(identity);
    await entered;
    expect(alive).toBe(false);
    expect(storage.values.get(PRIVATE_CLEANUP_KEY)).toMatchObject({ phase: "pending" });
    expect(storage.values.has(CLEANUP_KEY)).toBe(false);
    await expect(instance.claimReservedBinding(binding)).rejects.toThrow("already finalized");
    await expect(instance.claimArchiveUpload({
      schema_version: 1, upload_kind: "authoritative-archive", runner_nonce: "1".repeat(64),
      archive_sha256: "6".repeat(64), archive_bytes: 1, part_count: 1,
    })).rejects.toThrow("already been finalized");
    const upload = {
      schema_version: 1, upload_kind: "authoritative-archive", runner_nonce: "1".repeat(64),
      archive_sha256: "6".repeat(64), archive_bytes: 1, part_count: 1,
    };
    await expect(instance.commitArchiveUploadPart(upload, {})).rejects.toThrow("already finalized");
    await expect(instance.finalizeArchiveUpload(upload, "/assembled")).rejects.toThrow("already finalized");
    alive = true; // A key write/start already dispatched before the barrier.
    const currentInstanceIdle = sdkStorage.get("sleepAfter");
    expect(currentInstanceIdle).toBe(21600);
    expect(sdkStorage.has("sleepAfter")).toBe(true);
    acknowledge?.();
    expect(await cleanup).toMatchObject({ destruction_state: "confirmed" });
    expect(alive).toBe(true);
    expect(storage.values.get(ACTIVE_BINDING_KEY)).toEqual(binding);
    expect(storage.alarms.at(-1)).toBe(binding.cleanup_after_epoch_ms);
    await instance.alarm();
    expect(calls).toBe(1); // Early alarm cannot shift the original final sweep.
    const clock = vi.spyOn(Date, "now").mockReturnValue(binding.cleanup_after_epoch_ms as number);
    try {
      await instance.alarm();
      expect(calls).toBe(2);
      expect(alive).toBe(false);
      expect(storage.values.has(PRIVATE_CLEANUP_KEY)).toBe(false);
      expect(storage.values.get(CLEANUP_KEY)).toMatchObject({ destruction_state: "confirmed" });
    } finally { clock.mockRestore(); }
  });

  it("retains a pending barrier on failed destroy and retries without reporting confirmation", async () => {
    let fail = true;
    const { storage, instance, identity } = await admitted(() => (
      fail ? Promise.reject(new Error("cancelled RPC")) : Promise.resolve()
    ));
    await expect(instance.destroyPrivateBoundSandbox(identity)).rejects.toThrow("cancelled RPC");
    expect(storage.values.get(PRIVATE_CLEANUP_KEY)).toMatchObject({ phase: "pending" });
    expect(storage.values.has(CLEANUP_KEY)).toBe(false);
    expect(storage.values.has(ACTIVE_BINDING_KEY)).toBe(true);
    fail = false;
    await instance.alarm();
    expect(storage.values.get(PRIVATE_CLEANUP_KEY)).toMatchObject({ phase: "confirmed" });
    expect(storage.values.get(CLEANUP_KEY)).toMatchObject({ destruction_state: "confirmed" });
  });

  it("retains original nonce after a failed final sweep and retries before tombstone-only retention", async () => {
    let fail = false;
    const { storage, instance, binding, identity } = await admitted(() => (
      fail ? Promise.reject(new Error("sweep unavailable")) : Promise.resolve()
    ));
    await instance.destroyPrivateBoundSandbox(identity);
    const clock = vi.spyOn(Date, "now").mockReturnValue(binding.cleanup_after_epoch_ms as number);
    try {
      fail = true;
      await instance.alarm();
      expect(storage.values.has(PRIVATE_CLEANUP_KEY)).toBe(true);
      expect(storage.values.get(ACTIVE_BINDING_KEY)).toEqual(binding);
      expect(storage.alarms.at(-1)).toBe(Date.now() + 5 * 60_000);
      fail = false;
      await instance.alarm();
      expect(storage.values.has(PRIVATE_CLEANUP_KEY)).toBe(false);
      clock.mockReturnValue(Date.now() + 25 * 3600_000);
      await instance.alarm();
      expect(storage.values.has(ACTIVE_BINDING_KEY)).toBe(false);
      expect(storage.values.get(CLEANUP_KEY)).toEqual({ ...identity, destruction_state: "confirmed" });
    } finally { clock.mockRestore(); }
  });

  it("starts the barrier even when only the original private reaper sees abandonment", async () => {
    const destroy = vi.fn(() => Promise.resolve());
    const { storage, instance, binding } = await admitted(destroy);
    const clock = vi.spyOn(Date, "now").mockReturnValue(binding.cleanup_after_epoch_ms as number);
    try {
      await instance.alarm();
      expect(destroy).toHaveBeenCalledOnce();
      expect(storage.values.get(PRIVATE_CLEANUP_KEY)).toMatchObject({ phase: "confirmed" });
      await instance.alarm();
      expect(destroy).toHaveBeenCalledTimes(2);
      expect(storage.values.has(PRIVATE_CLEANUP_KEY)).toBe(false);
    } finally { clock.mockRestore(); }
  });

  it("refuses wrong identity before writing a barrier or destroying", async () => {
    const destroy = vi.fn(() => Promise.resolve());
    const { storage, instance, identity } = await admitted(destroy);
    await expect(instance.destroyPrivateBoundSandbox({ ...identity, attempt: 2 })).rejects.toThrow("identity mismatch");
    expect(storage.values.has(PRIVATE_CLEANUP_KEY)).toBe(false);
    expect(destroy).not.toHaveBeenCalled();
  });
  it("does not reap a prewarmed admitted replay at its shorter upload lease", async () => {
    const destroy = vi.fn(() => Promise.resolve());
    const { storage, instance, binding } = await admitted(destroy);
    await instance.claimArchiveUpload({
      schema_version: 1, upload_kind: "authoritative-archive", runner_nonce: "1".repeat(64),
      archive_sha256: "6".repeat(64), archive_bytes: 1, part_count: 1,
    });
    const uploadDeadline = storage.alarms.at(-1);
    if (uploadDeadline === undefined) throw new Error("upload alarm missing");
    expect(uploadDeadline).toBeLessThan(binding.cleanup_after_epoch_ms as number);
    const clock = vi.spyOn(Date, "now").mockReturnValue(uploadDeadline);
    try {
      await instance.alarm();
      expect(destroy).not.toHaveBeenCalled();
      expect(storage.values.has(PRIVATE_CLEANUP_KEY)).toBe(false);
      expect(storage.alarms.at(-1)).toBe(binding.cleanup_after_epoch_ms);
      clock.mockReturnValue(binding.cleanup_after_epoch_ms as number);
      await instance.alarm();
      expect(destroy).toHaveBeenCalledOnce();
    } finally { clock.mockRestore(); }
  });

  it("keeps pending absent-binding cleanup closed until storage confirmation succeeds", async () => {
    const storage = storageFixture();
    const destroy = vi.fn(() => Promise.resolve());
    const instance = receiptFixture(storage, destroy);
    const binding = activeBinding();
    const identity = { schema_version: 1 as const,
      replay_task_id: binding.replay_task_id as string, attempt: 1 };
    await instance.reserveCleanupIdentity(identity);
    storage.values.set(PRIVATE_CLEANUP_KEY, { ...identity, phase: "pending" });
    const original = storage.put.bind(storage);
    storage.put = (key, value) => key === CLEANUP_KEY
      ? Promise.reject(new Error("storage unavailable")) : original(key, value);
    await instance.alarm();
    expect(storage.values.has(PRIVATE_CLEANUP_KEY)).toBe(true);
    expect(storage.values.has(CLEANUP_KEY)).toBe(false);
    await expect(instance.claimReservedBinding(binding)).rejects.toThrow("already finalized");
    storage.put = original;
    await instance.alarm();
    expect(storage.values.has(PRIVATE_CLEANUP_KEY)).toBe(false);
    expect(storage.values.get(CLEANUP_KEY)).toMatchObject({ destruction_state: "confirmed" });
    expect(destroy).not.toHaveBeenCalled();
  });

  it("private status refuses closed cleanup before sandbox lookup but preserves stored receipts", async () => {
    const { storage, instance, identity } = await admitted(() => Promise.resolve());
    await instance.assertPrivateStatusAvailable();
    storage.values.set(PRIVATE_CLEANUP_KEY, { ...identity, phase: "pending" });
    await expect(instance.assertPrivateStatusAvailable()).rejects.toThrow("cleanup has begun");
    storage.values.set("authoritative-terminal-receipt:v1", {
      retained_until_epoch_ms: Date.now() + 60000, destruction_state: "pending",
    });
    await instance.assertPrivateStatusAvailable();
    storage.values.delete("authoritative-terminal-receipt:v1");
    storage.values.delete(PRIVATE_CLEANUP_KEY);
    storage.values.set(CLEANUP_KEY, { ...identity, destruction_state: "confirmed" });
    await expect(instance.assertPrivateStatusAvailable()).rejects.toThrow("cleanup has begun");
  });

});
