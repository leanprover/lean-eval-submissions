import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MARKER_PHASE_KEY, MARKER_SERVICE, MARKER_WORKFLOW, MarkerPhaseError, MarkerRecorder,
  appendMarkerJournal, claimMarkerJournal, markerBinding, markerCanonical, markerErrorCode,
  markerHash, markerJournalName, markerRequestBinding, newMarkerJournal, readMarkerPhaseRequest,
  readMarkerScope, type MarkerEvent, type MarkerPhaseStore,
} from "../src/marker-phase-evidence";
import { handleReplayRequest, type ReplayRuntimeEnv } from "../src/replay-app";
import { ReplayTerminalReceipt } from "../src/replay-terminal-receipt";
import type { ReplayAcceptanceRequest } from "../src/replay-contract";

const NONCE = "1".repeat(64);
const VERSION = "71d1eba9-a3eb-4361-ba60-e979fdc40999";
const SENTINEL = "AGE-SECRET-KEY-secret-sentinel-command-body-stack";

async function fixture() {
  const scope = {
    schema_version: 1, case: "a-match", service: MARKER_SERVICE, workflow: MARKER_WORKFLOW,
    commit: "a".repeat(40), run_id: "44690000000", run_attempt: "1",
    start_before: Date.now() + 900_000, retain_until: Date.now() + 1_800_000,
    nonce_sha256: await markerHash("lean-eval-marker-nonce-v1", NONCE),
    sandbox_name_sha256: await markerHash("lean-eval-marker-sandbox-v1", `r-${NONCE.slice(0, 61)}`),
    execution_profile_digest: "b".repeat(64), measurement_config_digest: "c".repeat(64), image_digest: `sha256:${"d".repeat(64)}`,
  };
  scope.retain_until = scope.start_before + 900_000;
  const env = {
    MARKER_PHASE_SCOPE: markerCanonical(scope), CF_VERSION_METADATA: { id: VERSION },
    DEPLOYMENT_ENVIRONMENT: "staging", DEPLOYED_COMMIT: scope.commit,
    REPLAY_ENABLED: "false", HISTORICAL_PUBLIC_REPLAY_ENABLED: "false", STAGING_ACCEPTANCE_ENABLED: "true",
    GITHUB_OIDC_ENVIRONMENT: "cloudflare-staging", GITHUB_OIDC_AUDIENCE: "lean-eval-replay-staging",
    REVIEWED_EXECUTION_PROFILE_DIGEST: scope.execution_profile_digest,
    REVIEWED_MEASUREMENT_CONFIG_DIGEST: scope.measurement_config_digest,
    REVIEWED_VM_IMAGE_DIGEST: scope.image_digest,
  } as ReplayRuntimeEnv;
  const ciphertext = new TextEncoder().encode("age-encryption.org/v1\nfixture");
  const sha = await crypto.subtle.digest("SHA-256", ciphertext);
  const body: ReplayAcceptanceRequest = {
    schema_version: 1, request_id: "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f", runner_nonce: NONCE,
    archive_ciphertext_sha256: [...new Uint8Array(sha)].map((byte) => byte.toString(16).padStart(2, "0")).join(""),
    ciphertext_base64: btoa(new TextDecoder().decode(ciphertext)), plaintext_identity_base64: btoa(SENTINEL), marker_sha256: "e".repeat(64),
  };
  const binding = await markerRequestBinding(env, body);
  const values = new Map<string, unknown>();
  const alarms: number[] = [];
  type Storage = {
    get<T>(key: string): Promise<T | undefined>;
    put(key: string, value: unknown): Promise<void>;
    setAlarm(value: number): Promise<void>;
    transaction<T>(action: (transaction: Storage) => Promise<T>): Promise<T>;
  };
  const storage: Storage = {
    get<T>(key: string): Promise<T | undefined> { return Promise.resolve(structuredClone(values.get(key)) as T | undefined); },
    put(key: string, value: unknown): Promise<void> { values.set(key, structuredClone(value)); return Promise.resolve(); },
    setAlarm(value: number): Promise<void> { alarms.push(value); return Promise.resolve(); },
    transaction<T>(action: (transaction: Storage) => Promise<T>): Promise<T> { return action(storage); },
  };
  const sandboxAccess = vi.fn(() => { throw new Error("journal must never access Sandbox"); });
  const store = Object.create(ReplayTerminalReceipt.prototype) as ReplayTerminalReceipt;
  Object.defineProperties(store, {
    ctx: { value: { storage } }, env: { value: { ...env, REPLAY_SANDBOX: { get: sandboxAccess, getByName: sandboxAccess } } },
  });
  const sandbox = {
    writeFile: vi.fn((path: string) => Promise.resolve({ success: true, path, timestamp: "fixture" })),
    exec: vi.fn(() => Promise.resolve({ success: true, command: "fixture", exitCode: 0, duration: 0, timestamp: "fixture",
      stdout: JSON.stringify({ schema_version: 1, archive_ciphertext_sha256: body.archive_ciphertext_sha256,
        marker_sha256: body.marker_sha256, network_probe: "blocked", architecture: "x86_64", kernel_release: "kernel", cpu_model: "cpu" }), stderr: "" })),
    destroy: vi.fn(() => Promise.resolve()),
  };
  const factory = vi.fn(() => sandbox);
  const auth = vi.fn(() => Promise.resolve());
  const dependencies = { authenticate: auth, authenticateMarker: auth, markerStore: () => store, sandbox: factory };
  const request = (suffix: string, value: unknown) => new Request(`https://example.test/api/v1/staging-acceptance${suffix}`, {
    method: "POST", body: markerCanonical(value),
  });
  const proofBody = { schema_version: 1, request_commitment: binding.request_commitment };
  return { scope, env, body, binding, values, alarms, store, sandboxAccess, sandbox, factory, dependencies, request, proofBody };
}

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("finite source-free marker journal", () => {
  it("uses canonical shared hash vectors and the actual truncated SDK name", async () => {
    vi.spyOn(Date, "now").mockReturnValue(1_787_395_200_000);
    expect(await markerHash("lean-eval-marker-nonce-v1", NONCE)).toBe("95952b662fc203ba46e3d5b6d12860af1be51d426d5da05d2134524de78d2042");
    const f = await fixture();
    expect(f.binding).toEqual({ schema_version: 1,
      scope_sha256: "1180d606eb8888de0f2a10fdec4b1eea827c19428fc3cbd05f9c153ba2c56942",
      worker_version: VERSION, marker_instance_id: "122ec2b7558bd169dab2bffe6771c090c5d612240aff4146b34bd15df686bb55",
      request_commitment: "e569308e04b5ac5a33fafa1d1ce98c62f2016a5bebef22ac3095bd03efcdf64c" });
    const sameName = `${NONCE.slice(0, 61)}222`;
    expect(await markerHash("lean-eval-marker-sandbox-v1", `r-${sameName.slice(0, 61)}`)).toBe(f.scope.sandbox_name_sha256);
    await expect(markerRequestBinding(f.env, { ...f.body, runner_nonce: sameName })).rejects.toThrow(MarkerPhaseError);
    const other = { ...f.env, CF_VERSION_METADATA: { id: "00000000-0000-4000-8000-000000000000" } };
    expect((await markerRequestBinding(other, f.body)).marker_instance_id).not.toBe(f.binding.marker_instance_id);
    expect(await markerJournalName(f.env)).toMatch(/^marker-phase-[0-9a-f]{64}$/);
  });

  it("proves persistence across instances and reads without Sandbox or alarm renewal", async () => {
    const f = await fixture();
    const proof = await f.store.prepareMarkerPhase(f.binding);
    const alarmCount = f.alarms.length;
    expect(await f.store.readMarkerPhase(f.binding)).toEqual(proof);
    expect(f.alarms).toHaveLength(alarmCount);
    expect(f.sandboxAccess).not.toHaveBeenCalled();
    await expect(f.store.prepareMarkerPhase(f.binding)).rejects.toThrow(MarkerPhaseError);
    await f.store.claimMarkerPhase(f.binding);
    await expect(f.store.claimMarkerPhase(f.binding)).rejects.toThrow(MarkerPhaseError);
    await expect(f.store.readMarkerPhase({ ...f.binding, request_commitment: "0".repeat(64) })).rejects.toThrow(MarkerPhaseError);
  });

  it("refuses late appends after fencing or expiry and alarms retain a spent storage-only tombstone", async () => {
    const f = await fixture();
    await f.store.prepareMarkerPhase(f.binding);
    await f.store.claimMarkerPhase(f.binding);
    let release = () => { /* Assigned below. */ };
    const wait = new Promise<void>((resolve) => { release = resolve; });
    const late = wait.then(() => f.store.appendMarkerPhase(f.binding, { operation: "sandbox_handle", state: "started" }));
    await f.store.fenceMarkerPhase(f.binding);
    release();
    await expect(late).rejects.toThrow(MarkerPhaseError);
    vi.spyOn(Date, "now").mockReturnValue(f.scope.retain_until);
    await f.store.alarm();
    expect(f.values.get(MARKER_PHASE_KEY)).toMatchObject({ state: "expired", events: [] });
    await expect(f.store.prepareMarkerPhase(f.binding)).rejects.toThrow(MarkerPhaseError);
    await expect(f.store.appendMarkerPhase(f.binding, { operation: "sandbox_handle", state: "started" })).rejects.toThrow(MarkerPhaseError);
    expect(f.sandboxAccess).not.toHaveBeenCalled();
  });

  it("rejects unknown fields, invalid transition/duration/code and bounded request excess", async () => {
    const f = await fixture();
    const active = claimMarkerJournal(newMarkerJournal(f.binding, readMarkerScope(f.env)));
    for (const event of [
      { operation: "exec_acceptance", state: "started" },
      { operation: "sandbox_handle", state: "started", message: SENTINEL },
      { operation: "sandbox_handle", state: "succeeded", elapsed_ms: -1 },
    ]) expect(() => appendMarkerJournal(structuredClone(active), event as MarkerEvent)).toThrow(MarkerPhaseError);
    const started = appendMarkerJournal(active, { operation: "sandbox_handle", state: "started" });
    for (const duration of [NaN, Infinity, -1, 0.5, 600001]) {
      expect(() => appendMarkerJournal(structuredClone(started), { operation: "sandbox_handle", state: "failed", elapsed_ms: duration })).toThrow(MarkerPhaseError);
    }
    expect(() => appendMarkerJournal(structuredClone(started), { operation: "sandbox_handle", state: "failed", elapsed_ms: 0, code: SENTINEL })).toThrow(MarkerPhaseError);
    await expect(readMarkerPhaseRequest(f.request("/phase-status", { ...f.proofBody, secret: SENTINEL }))).rejects.toThrow();
    await expect(readMarkerPhaseRequest(new Request("https://example.test", { method: "POST", body: "x".repeat(1025) }))).rejects.toThrow();
    expect(markerErrorCode({ get code() { throw new Error(SENTINEL); } })).toBe("unclassified");
    expect(markerErrorCode({ code: "RPC_TRANSPORT_ERROR", message: SENTINEL })).toBe("RPC_TRANSPORT_ERROR");
  });

  it("proof/status cannot create a Sandbox; starts require proof and collision-safe identity", async () => {
    const f = await fixture();
    const before = await handleReplayRequest(f.request("", f.body), f.env, f.dependencies);
    expect(before.status).toBe(500);
    expect(f.factory).not.toHaveBeenCalled();
    for (const path of ["/api/v1/replay", "/api/v1/historical-public-replay", "/api/v1/staging-archive-acceptance/archive-part", "/api/v1/staging-archive-acceptance"]) {
      const response = await handleReplayRequest(new Request(`https://example.test${path}`, { method: "POST", body: "{}" }), f.env, f.dependencies);
      expect(response.status).toBe(404);
    }
    expect(f.factory).not.toHaveBeenCalled();
    for (const suffix of ["/phase-proof", "/phase-status"]) {
      expect((await handleReplayRequest(f.request(suffix, f.proofBody), f.env, f.dependencies)).status).toBe(200);
    }
    expect(f.factory).not.toHaveBeenCalled();
    const collision = { ...f.body, runner_nonce: `${NONCE.slice(0, 61)}222` };
    expect((await handleReplayRequest(f.request("", collision), f.env, f.dependencies)).status).toBe(500);
    expect(f.factory).not.toHaveBeenCalled();
    f.dependencies.authenticateMarker = vi.fn(() => Promise.reject(new Error(SENTINEL)));
    expect((await handleReplayRequest(f.request("/phase-status", f.proofBody), f.env, f.dependencies)).status).toBe(500);
    expect(f.factory).not.toHaveBeenCalled();
  });

  it("records all three writes, the unchanged exec timeout, validation and independent destroy", async () => {
    const f = await fixture();
    await f.store.prepareMarkerPhase(f.binding);
    const response = await handleReplayRequest(f.request("", f.body), f.env, f.dependencies);
    expect(response.status).toBe(200);
    expect(f.sandbox.writeFile).toHaveBeenCalledTimes(3);
    expect(f.sandbox.exec).toHaveBeenCalledWith("/opt/lean-eval/replay-staging-acceptance", { timeout: 120000 });
    expect(f.sandbox.destroy).toHaveBeenCalledTimes(1);
    const journal = await f.store.readMarkerPhase(f.binding);
    expect(journal).toMatchObject({ state: "terminal", operation_outcome: "succeeded", destruction_outcome: "succeeded" });
    expect(journal.events.map((event) => `${event.operation}:${event.state}`)).toEqual([
      "sandbox_handle:started", "sandbox_handle:succeeded", "write_ciphertext:started", "write_ciphertext:succeeded",
      "write_identity:started", "write_identity:succeeded", "write_expectation:started", "write_expectation:succeeded",
      "exec_acceptance:started", "exec_acceptance:succeeded", "validate_evidence:started", "validate_evidence:succeeded", "destroy:started", "destroy:succeeded",
    ]);
    expect(JSON.stringify(journal)).not.toContain(NONCE);
    expect(JSON.stringify(journal)).not.toContain(SENTINEL);
    expect(JSON.stringify(await response.json())).not.toContain(NONCE);
    expect((await handleReplayRequest(f.request("", f.body), f.env, f.dependencies)).status).toBe(500);
    expect(f.factory).toHaveBeenCalledTimes(1);
  });

  it("exposes a pending first write and can observe/fence it without another execution", async () => {
    const f = await fixture();
    await f.store.prepareMarkerPhase(f.binding);
    let release = () => { /* Assigned below. */ };
    let entered = () => { /* Assigned below. */ };
    const firstWrite = new Promise<void>((resolve) => { entered = resolve; });
    const pending = new Promise<void>((resolve) => { release = resolve; });
    f.sandbox.writeFile.mockImplementationOnce(async (path) => {
      entered(); await pending; return { success: true, path, timestamp: "fixture" };
    });
    const running = handleReplayRequest(f.request("", f.body), f.env, f.dependencies);
    await firstWrite;
    const journal = await f.store.readMarkerPhase(f.binding);
    expect(journal.events.at(-1)).toEqual({ operation: "write_ciphertext", state: "started" });
    expect(f.sandbox.exec).not.toHaveBeenCalled();
    await f.store.fenceMarkerPhase(f.binding);
    release();
    expect((await running).status).toBe(500);
    expect(f.sandbox.writeFile).toHaveBeenCalledTimes(1);
    expect(f.sandbox.exec).not.toHaveBeenCalled();
    expect(f.sandbox.destroy).toHaveBeenCalledTimes(1);
    expect((await f.store.readMarkerPhase(f.binding)).state).toBe("fenced");
  });

  it("retains separate operation and destroy failures without raw error data", async () => {
    const f = await fixture();
    await f.store.prepareMarkerPhase(f.binding);
    f.sandbox.exec.mockRejectedValueOnce(Object.assign(new Error(SENTINEL), { code: "RPC_TRANSPORT_ERROR", context: { secret: SENTINEL } }));
    f.sandbox.destroy.mockRejectedValueOnce(new Error(SENTINEL));
    const response = await handleReplayRequest(f.request("", f.body), f.env, f.dependencies);
    expect(response.status).toBe(500);
    const journal = await f.store.readMarkerPhase(f.binding);
    expect(journal).toMatchObject({ state: "terminal", operation_outcome: "failed", destruction_outcome: "failed" });
    expect(journal.events.find((event) => event.operation === "exec_acceptance" && event.state === "failed")?.code).toBe("RPC_TRANSPORT_ERROR");
    expect(JSON.stringify(journal) + JSON.stringify(await response.json())).not.toContain(SENTINEL);
    expect(f.sandbox.exec).toHaveBeenCalledTimes(1);
  });

  it.each(["exec_acceptance", "destroy"] as const)("persists a pending %s while inert status reads cannot advance it", async (operation) => {
    const f = await fixture();
    await f.store.prepareMarkerPhase(f.binding);
    let entered = () => { /* Assigned below. */ };
    let release = () => { /* Assigned below. */ };
    const entry = new Promise<void>((resolve) => { entered = resolve; });
    const gate = new Promise<void>((resolve) => { release = resolve; });
    if (operation === "exec_acceptance") {
      const original = f.sandbox.exec.getMockImplementation();
      if (original === undefined) throw new Error("missing exec fixture");
      f.sandbox.exec.mockImplementation(async () => { entered(); await gate; return original(); });
    } else f.sandbox.destroy.mockImplementation(async () => { entered(); await gate; });
    const running = handleReplayRequest(f.request("", f.body), f.env, f.dependencies);
    await entry;
    expect((await f.store.readMarkerPhase(f.binding)).events.at(-1)).toEqual({ operation, state: "started" });
    const response = await handleReplayRequest(f.request("/phase-status", f.proofBody), f.env, f.dependencies);
    expect(response.status).toBe(200);
    expect(f.factory).toHaveBeenCalledTimes(1);
    expect(f.sandbox.exec).toHaveBeenCalledTimes(1);
    expect(f.sandbox.destroy).toHaveBeenCalledTimes(operation === "destroy" ? 1 : 0);
    release();
    expect((await running).status).toBe(200);
    expect(f.sandbox.destroy).toHaveBeenCalledTimes(1);
  });

  it("does not confirm destruction when an SDK promise rejects with an undefined value", async () => {
    const f = await fixture();
    await f.store.prepareMarkerPhase(f.binding);
    f.sandbox.destroy.mockRejectedValueOnce(undefined);
    const response = await handleReplayRequest(f.request("", f.body), f.env, f.dependencies);
    expect(response.status).toBe(500);
    expect(await response.json()).toMatchObject({ error: "marker_failed", reason: "sandbox_destroy_failed" });
    expect(await f.store.readMarkerPhase(f.binding)).toMatchObject({ operation_outcome: "succeeded", destruction_outcome: "failed" });
  });

  it("a timed-out journal append cannot dispatch runtime work or commit after the controller fence", async () => {
    const f = await fixture();
    await f.store.prepareMarkerPhase(f.binding);
    await f.store.claimMarkerPhase(f.binding);
    vi.useFakeTimers();
    let release = () => { /* Assigned below. */ };
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const delayed = Object.create(f.store) as MarkerPhaseStore;
    delayed.appendMarkerPhase = async (binding, event) => { await gate; return f.store.appendMarkerPhase(binding, event); };
    const sideEffect = vi.fn(() => Promise.resolve());
    const result = new MarkerRecorder(delayed, f.binding).operation("sandbox_handle", sideEffect);
    const refused = expect(result).rejects.toThrow(MarkerPhaseError);
    await vi.advanceTimersByTimeAsync(2001);
    await refused;
    expect(sideEffect).not.toHaveBeenCalled();
    await f.store.fenceMarkerPhase(f.binding);
    release();
    await Promise.resolve();
    expect((await f.store.readMarkerPhase(f.binding)).events).toEqual([]);
  });

  it("journal failures cannot suppress actual destruction and recorder uses a local monotonic clock", async () => {
    const f = await fixture();
    await f.store.prepareMarkerPhase(f.binding);
    await f.store.claimMarkerPhase(f.binding);
    let now = 10;
    const recorder = new MarkerRecorder(f.store, f.binding, () => now);
    await recorder.operation("sandbox_handle", () => { now += 7.9; return Promise.resolve(); });
    expect((await f.store.readMarkerPhase(f.binding)).events.at(-1)?.elapsed_ms).toBe(7);
    const broken = Object.create(f.store) as MarkerPhaseStore;
    broken.appendMarkerPhase = () => Promise.reject(new Error(SENTINEL));
    const cleanup = vi.fn(() => Promise.resolve());
    await expect(new MarkerRecorder(broken, f.binding).operation("destroy", cleanup, true)).rejects.toThrow();
    expect(cleanup).toHaveBeenCalledTimes(1);
    const unbounded = { ...f.env, MARKER_PHASE_SCOPE: `${f.env.MARKER_PHASE_SCOPE ?? ""} ` };
    expect(() => readMarkerScope(unbounded)).toThrow(MarkerPhaseError);
    await expect(markerBinding(f.env, SENTINEL)).rejects.toThrow(MarkerPhaseError);
    await expect(markerBinding(f.env, ["a".repeat(64)])).rejects.toThrow(MarkerPhaseError);
  });
});
