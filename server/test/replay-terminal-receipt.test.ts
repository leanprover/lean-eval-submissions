import { describe, expect, it, vi } from "vitest";

import { ReplayTerminalReceipt } from "../src/replay-terminal-receipt";

const ACTIVE_BINDING_KEY = "authoritative-active-binding:v1";
const CLEANUP_KEY = "authoritative-sandbox-cleanup:v1";

type StorageFixture = {
  values: Map<string, unknown>;
  alarms: number[];
  get(key: string): Promise<unknown>;
  put(key: string, value: unknown): Promise<void>;
  delete(key: string | string[]): Promise<void>;
  setAlarm(value: number): Promise<void>;
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
    cleanup_after_epoch_ms: now + 7 * 60 * 60 * 1000,
    retained_until_epoch_ms: now + 24 * 60 * 60 * 1000,
  };
}

describe("durable replay sandbox cleanup", () => {
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

  it("deletes only a confirmed cleanup identity after bounded retention", async () => {
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

    expect(storage.values.size).toBe(0);
    expect(destroyCalls).toBe(0);
  });
});
