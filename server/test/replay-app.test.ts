import { describe, expect, it, vi } from "vitest";

import { handleReplayRequest, type ReplayRuntimeEnv } from "../src/replay-app";
import { canonicalHistoricalPublicHandoff } from "../src/historical-public-executor-contract";

const PROFILE_DIGEST = "3".repeat(64);
const MEASUREMENT_DIGEST = "4".repeat(64);
const VM_IMAGE_DIGEST = `sha256:${"5".repeat(64)}`;

async function input(): Promise<Record<string, unknown>> {
  const ciphertext = btoa("age-encryption.org/v1\nfixture");
  const bytes = Uint8Array.from(atob(ciphertext), (character) => character.charCodeAt(0));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return {
    schema_version: 1,
    request_id: "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f",
    runner_nonce: "1".repeat(64),
    archive_ciphertext_sha256: [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, "0")).join(""),
    ciphertext_base64: ciphertext,
    plaintext_identity_base64: btoa("AGE-SECRET-KEY-1FIXTURE"),
    marker_sha256: "2".repeat(64),
  };
}

async function archiveInput(): Promise<Record<string, unknown>> {
  const ciphertext = btoa("age-encryption.org/v1\naccepted-fixture");
  const bytes = Uint8Array.from(atob(ciphertext), (character) => character.charCodeAt(0));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return {
    schema_version: 1,
    request_id: "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f",
    runner_nonce: "1".repeat(64),
    submission_id: "01a02cb4-5e7c-7fb3-a4ab-b6fabbb72584",
    archive_ciphertext_sha256: [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, "0")).join(""),
    plaintext_tar_sha256: "2".repeat(64),
    plaintext_tar_size: 712,
    ciphertext_base64: ciphertext,
    plaintext_identity_base64: btoa("AGE-SECRET-KEY-1FIXTURE"),
  };
}

async function authoritativeInput(): Promise<Record<string, unknown>> {
  const ciphertext = btoa("age-encryption.org/v1\nauthoritative-fixture");
  const bytes = Uint8Array.from(atob(ciphertext), (character) => character.charCodeAt(0));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const archiveDigest = [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
  const submissionId = "01a02cb4-5e7c-7fb3-a4ab-b6fabbb72584";
  return {
    schema_version: 1,
    runner_nonce: "1".repeat(64),
    request: {
      replay_task_id: `rt1_${"2".repeat(64)}`,
      attempt: 1,
      execution_profile_digest: PROFILE_DIGEST,
      measurement_config_digest: MEASUREMENT_DIGEST,
      execution_profile: { vm_image_digest: VM_IMAGE_DIGEST },
      source: {
        visibility: "private",
        archive: {
          submission_id: submissionId,
          archive_ciphertext_sha256: archiveDigest,
        },
      },
      result: { submission_id: submissionId },
    },
    archive_expectation: {
      schema_version: 1,
      submission_id: submissionId,
      archive_ciphertext_sha256: archiveDigest,
      plaintext_tar_sha256: "3".repeat(64),
      plaintext_tar_size: 712,
    },
    ciphertext_base64: ciphertext,
    plaintext_identity_base64: btoa("AGE-SECRET-KEY-1FIXTURE"),
  };
}

async function historicalPublicInput(): Promise<Record<string, unknown>> {
  const sourceArchive = new TextEncoder().encode("historical public source archive");
  const archiveHash = await crypto.subtle.digest("SHA-256", sourceArchive);
  const archiveDigest = [...new Uint8Array(archiveHash)]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
  const handoff = {
    schema_version: 1,
    kind: "historical_public_runner_handoff",
    contract: "historical_public_runner_v1",
    contract_sha256: "6".repeat(64),
    plan_sha256: "7".repeat(64),
    profile_matrix_sha256: "8".repeat(64),
    request_id: `prr_${"9".repeat(64)}`,
    source: {
      repository: "example/source",
      commit: "a".repeat(40),
      tree: "b".repeat(40),
      visibility: "public",
      archive_format: "git_archive_tar_gzip_v1",
      archive_member_prefix: "source",
      archive_sha256: archiveDigest,
      archive_size_bytes: sourceArchive.byteLength,
    },
    benchmark: {},
    result: { result_id: `r2_${"c".repeat(64)}` },
    profile: {},
    checker: "nanoda",
    network: {},
    untrusted_environment: {},
  };
  const handoffHash = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(canonicalHistoricalPublicHandoff(handoff)),
  );
  return {
    schema_version: 1,
    runner_nonce: "1".repeat(64),
    replay_task_id: `rt1_${"2".repeat(64)}`,
    attempt: 1,
    handoff_sha256: [...new Uint8Array(handoffHash)]
      .map((byte) => byte.toString(16).padStart(2, "0")).join(""),
    source_archive_sha256: archiveDigest,
    execution_profile_digest: PROFILE_DIGEST,
    measurement_config_digest: MEASUREMENT_DIGEST,
    vm_image_digest: VM_IMAGE_DIGEST,
    handoff,
    source_archive_base64: btoa(String.fromCharCode(...sourceArchive)),
  };
}

function historicalPublicRunnerVerdict(body: Record<string, unknown>): Record<string, unknown> {
  const handoff = body.handoff as Record<string, unknown>;
  const result = handoff.result as Record<string, unknown>;
  return {
    schema_version: 1,
    request_id: handoff.request_id,
    result_id: result.result_id,
    execution_outcome: "completed",
    checker_outcome: "accepted",
    failure_reason: null,
    statistics: {
      checker_wall_time_ms: 10,
      checker_retired_instructions: { status: "measured", value: 20 },
      build_wall_time_ms: 30,
      build_retired_instructions: { status: "measured", value: 40 },
      lines_of_code: 2,
      file_count: 1,
    },
  };
}

function historicalStatusInput(body: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(body).filter(
    ([field]) => field !== "handoff" && field !== "source_archive_base64",
  ));
}

function historicalProcessBindingInput(body: Record<string, unknown>): Record<string, unknown> {
  const handoff = body.handoff as Record<string, unknown>;
  const result = handoff.result as Record<string, unknown>;
  return {
    ...historicalStatusInput(body),
    request_id: handoff.request_id,
    result_id: result.result_id,
  };
}

function authoritativeStatusInput(body: Record<string, unknown>): Record<string, unknown> {
  const execution = body.request as Record<string, unknown>;
  const profile = execution.execution_profile as Record<string, unknown>;
  return {
    schema_version: 1,
    runner_nonce: body.runner_nonce,
    replay_task_id: execution.replay_task_id,
    attempt: execution.attempt,
    execution_profile_digest: execution.execution_profile_digest,
    measurement_config_digest: execution.measurement_config_digest,
    vm_image_digest: profile.vm_image_digest,
  };
}

function activeBindingForTest(binding: Record<string, unknown>): Record<string, unknown> {
  const now = Date.now();
  return {
    ...binding,
    cleanup_after_epoch_ms: now + 7 * 60 * 60 * 1000,
    retained_until_epoch_ms: now + 24 * 60 * 60 * 1000,
  };
}

function acceptedVerdict(body: Record<string, unknown>): Record<string, unknown> {
  const execution = body.request as Record<string, unknown>;
  return {
    schema_version: 1,
    replay_task_id: execution.replay_task_id,
    attempt: execution.attempt,
    execution_outcome: "completed",
    checker_outcome: "accepted",
    failure_reason: null,
    statistics: {
      checker_wall_time_ms: 10,
      checker_retired_instructions: { status: "measured", value: 20 },
      build_wall_time_ms: 30,
      build_retired_instructions: { status: "measured", value: 40 },
      lines_of_code: 2,
      file_count: 1,
    },
  };
}

function terminalReceiptStore(initialBinding?: Record<string, unknown>) {
  let binding: unknown = initialBinding === undefined
    ? null
    : activeBindingForTest(initialBinding);
  let receipt: unknown = null;
  return {
    readBinding: () => Promise.resolve(binding),
    claimBinding: (value: unknown) => {
      if (binding === null) binding = value;
      return Promise.resolve(binding);
    },
    readReceipt: () => Promise.resolve(receipt),
    prepareReceipt: (value: unknown) => {
      if (receipt === null) receipt = value;
      return Promise.resolve(receipt);
    },
    confirmReceipt: () => {
      if (typeof receipt !== "object" || receipt === null || Array.isArray(receipt)) {
        return Promise.reject(new Error("receipt is unavailable"));
      }
      receipt = { ...receipt, destruction_state: "confirmed" };
      return Promise.resolve(receipt);
    },
  };
}

const ENV = {
  DEPLOYED_COMMIT: "a".repeat(40),
  DEPLOYMENT_ENVIRONMENT: "staging",
  REPLAY_ENABLED: "false",
  HISTORICAL_PUBLIC_REPLAY_ENABLED: "false",
  STAGING_ACCEPTANCE_ENABLED: "true",
  STAGING_MEMORY_LIMIT_BYTES: "12884901888",
  PRODUCTION_MEMORY_GATE_BYTES: "12884901888",
  REVIEWED_EXECUTION_PROFILE_DIGEST: "0".repeat(64),
  REVIEWED_MEASUREMENT_CONFIG_DIGEST: "0".repeat(64),
  REVIEWED_VM_IMAGE_DIGEST: `sha256:${"0".repeat(64)}`,
  GITHUB_OIDC_AUDIENCE: "lean-eval-replay-staging",
  GITHUB_OIDC_ENVIRONMENT: "replay-staging",
} as ReplayRuntimeEnv;

const REVIEWED_ENV = {
  ...ENV,
  REVIEWED_EXECUTION_PROFILE_DIGEST: PROFILE_DIGEST,
  REVIEWED_MEASUREMENT_CONFIG_DIGEST: MEASUREMENT_DIGEST,
  REVIEWED_VM_IMAGE_DIGEST: VM_IMAGE_DIGEST,
} as ReplayRuntimeEnv;

describe("Cloudflare replay executor", () => {
  it("refuses the authoritative route before authentication while disabled", async () => {
    let authenticated = false;
    const response = await handleReplayRequest(new Request("https://example.test/api/v1/replay", {
      method: "POST",
      body: "{}",
    }), ENV, {
      authenticate: () => {
        authenticated = true;
        return Promise.resolve();
      },
      sandbox: () => { throw new Error("sandbox must remain unreachable"); },
    });
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "replay_disabled" });
    expect(authenticated).toBe(false);
  });

  it("keeps historical public replay separately disabled before authentication", async () => {
    for (const path of [
      "/api/v1/historical-public-replay",
      "/api/v1/historical-public-replay/status",
      "/api/v1/historical-public-replay/cleanup",
      "/api/v1/historical-public-replay/cleanup-reservation",
    ]) {
      let authenticated = false;
      const response = await handleReplayRequest(new Request(
        `https://example.test${path}`,
        { method: "POST", body: "{}" },
      ), REVIEWED_ENV, {
        authenticate: () => {
          authenticated = true;
          return Promise.resolve();
        },
        sandbox: () => { throw new Error("sandbox must remain unreachable"); },
      });
      expect(response.status).toBe(503);
      expect(await response.json()).toEqual({
        error: "historical_public_replay_disabled",
      });
      expect(authenticated).toBe(false);
    }
  });

  it("accepts attempt four and rejects attempt five across status and cleanup", async () => {
    const fourthAttempt = await historicalPublicInput();
    fourthAttempt.attempt = 4;
    const fourthStatus = historicalStatusInput(fourthAttempt);
    const fourthBinding = activeBindingForTest(
      historicalProcessBindingInput(fourthAttempt),
    );
    let recoveryStoreLookups = 0;
    let receiptStoreLookups = 0;
    const enabled = { ...REVIEWED_ENV, HISTORICAL_PUBLIC_REPLAY_ENABLED: "true" };
    const dependencies = {
      authenticate: () => Promise.resolve(),
      sandbox: () => ({
        writeFile: (path: string) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
        exec: () => { throw new Error("blocking exec must remain unreachable"); },
        getProcess: () => Promise.resolve({
          getStatus: () => Promise.resolve("running"),
        } as never),
        destroy: () => Promise.resolve(),
      }),
      receiptStore: () => {
        receiptStoreLookups += 1;
        return {
          readBinding: () => Promise.resolve(fourthBinding),
          claimBinding: (value: unknown) => Promise.resolve(value),
          readReceipt: () => Promise.resolve(null),
          prepareReceipt: (value: unknown) => Promise.resolve(value),
          confirmReceipt: () => Promise.reject(new Error("receipt is unavailable")),
        };
      },
      recoveryStore: () => {
        recoveryStoreLookups += 1;
        return {
          reserveCleanupIdentity: (identity: unknown) => Promise.resolve(identity),
          destroyBoundSandbox: (identity: unknown) => Promise.resolve({
            ...(identity as Record<string, unknown>),
            destruction_state: "confirmed",
          }),
        };
      },
    };
    const cleanupIdentity = {
      schema_version: 1,
      replay_task_id: fourthAttempt.replay_task_id,
      attempt: fourthAttempt.attempt,
    };

    const reservation = await handleReplayRequest(new Request(
      "https://example.test/api/v1/historical-public-replay/cleanup-reservation",
      { method: "POST", body: JSON.stringify(cleanupIdentity) },
    ), enabled, dependencies);
    expect(reservation.status).toBe(200);
    expect(await reservation.json()).toEqual({ ...cleanupIdentity, status: "reserved" });

    const cleanup = await handleReplayRequest(new Request(
      "https://example.test/api/v1/historical-public-replay/cleanup",
      { method: "POST", body: JSON.stringify(cleanupIdentity) },
    ), enabled, dependencies);
    expect(cleanup.status).toBe(200);
    expect(await cleanup.json()).toEqual({ ...cleanupIdentity, destruction: "confirmed" });

    const status = await handleReplayRequest(new Request(
      "https://example.test/api/v1/historical-public-replay/status",
      { method: "POST", body: JSON.stringify(fourthStatus) },
    ), enabled, dependencies);
    expect(status.status).toBe(202);
    expect(await status.json()).toEqual({
      schema_version: 1,
      replay_task_id: fourthAttempt.replay_task_id,
      attempt: 4,
      status: "running",
    });

    const fifthIdentity = { ...cleanupIdentity, attempt: 5 };
    const fifthStatus = { ...fourthStatus, attempt: 5 };
    for (const [path, body] of [
      ["/api/v1/historical-public-replay/cleanup-reservation", fifthIdentity],
      ["/api/v1/historical-public-replay/cleanup", fifthIdentity],
      ["/api/v1/historical-public-replay/status", fifthStatus],
    ] as const) {
      const response = await handleReplayRequest(new Request(
        `https://example.test${path}`,
        { method: "POST", body: JSON.stringify(body) },
      ), enabled, dependencies);
      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ error: "invalid_request" });
    }
    expect(recoveryStoreLookups).toBe(2);
    expect(receiptStoreLookups).toBe(1);
  });

  it("idempotently starts and polls one historical handoff through confirmed destruction", async () => {
    const body = await historicalPublicInput();
    const writes = new Map<string, string>();
    const commands: string[] = [];
    const processIds: string[] = [];
    let processStarted = false;
    let processStatus: "running" | "completed" = "running";
    let logReads = 0;
    let destroyCalls = 0;
    let claimedBinding: unknown = null;
    let storedReceipt: unknown = null;
    const receipts = {
      readBinding: () => Promise.resolve(claimedBinding),
      claimBinding: (value: unknown) => {
        if (claimedBinding === null) claimedBinding = value;
        return Promise.resolve(claimedBinding);
      },
      readReceipt: () => Promise.resolve(storedReceipt),
      prepareReceipt: (value: unknown) => {
        if (storedReceipt === null) storedReceipt = value;
        return Promise.resolve(storedReceipt);
      },
      confirmReceipt: () => {
        storedReceipt = {
          ...(storedReceipt as Record<string, unknown>),
          destruction_state: "confirmed",
        };
        return Promise.resolve(storedReceipt);
      },
    };
    const process = {
      getStatus: () => Promise.resolve(processStatus),
      getLogs: () => {
        logReads += 1;
        return Promise.resolve({
          stdout: JSON.stringify(historicalPublicRunnerVerdict(body)),
          stderr: "",
        });
      },
    };
    const sandbox = {
      writeFile: (path: string, contents: string) => {
        writes.set(path, contents);
        return Promise.resolve({ success: true, path, timestamp: "fixture" });
      },
      exec: () => { throw new Error("blocking exec must remain unreachable"); },
      getProcess: () => Promise.resolve(processStarted ? process as never : null),
      startProcess: (command: string, options?: { processId?: string }) => {
        processStarted = true;
        commands.push(command);
        processIds.push(options?.processId ?? "");
        return Promise.resolve(process as never);
      },
      destroy: () => {
        destroyCalls += 1;
        return Promise.resolve();
      },
    };
    const dependencies = {
      authenticate: () => Promise.resolve(),
      sandbox: () => sandbox,
      receiptStore: () => receipts,
    };
    const enabled = { ...REVIEWED_ENV, HISTORICAL_PUBLIC_REPLAY_ENABLED: "true" };
    const startRequest = () => new Request(
      "https://example.test/api/v1/historical-public-replay",
      { method: "POST", body: JSON.stringify(body) },
    );
    const statusRequest = () => new Request(
      "https://example.test/api/v1/historical-public-replay/status",
      { method: "POST", body: JSON.stringify(historicalStatusInput(body)) },
    );

    const start = await handleReplayRequest(startRequest(), enabled, dependencies);
    expect(start.status).toBe(202);
    expect(await start.json()).toEqual({
      schema_version: 1,
      replay_task_id: body.replay_task_id,
      attempt: body.attempt,
      status: "running",
    });
    expect(commands).toHaveLength(1);
    expect(commands[0]).toContain("/opt/lean-eval/historical-public-runner");
    expect(processIds).toEqual(["lean-eval-historical-public"]);
    expect(writes.get("/workspace/historical-public-request.json"))
      .toBe(canonicalHistoricalPublicHandoff(body.handoff));
    expect(writes.get("/workspace/historical-public-source.tar.gz.b64"))
      .toBe(body.source_archive_base64);
    expect(claimedBinding).toMatchObject(historicalProcessBindingInput(body));
    expect(typeof (claimedBinding as Record<string, unknown>).retained_until_epoch_ms)
      .toBe("number");
    expect(destroyCalls).toBe(0);

    const duplicateStart = await handleReplayRequest(startRequest(), enabled, dependencies);
    expect(duplicateStart.status).toBe(202);
    expect(commands).toHaveLength(1);
    expect(writes).toHaveLength(2);

    const running = await handleReplayRequest(statusRequest(), enabled, dependencies);
    expect(running.status).toBe(202);
    expect(await running.json()).toMatchObject({ status: "running" });
    expect(destroyCalls).toBe(0);

    processStatus = "completed";
    const terminal = await handleReplayRequest(statusRequest(), enabled, dependencies);
    expect(terminal.status).toBe(200);
    const terminalBody = await terminal.json();
    expect(terminalBody).toMatchObject({
      contract: "historical_public_executor_v1",
      replay_task_id: body.replay_task_id,
      attempt: body.attempt,
      runner_nonce: body.runner_nonce,
      handoff_sha256: body.handoff_sha256,
      source_archive_sha256: body.source_archive_sha256,
      execution_profile_digest: PROFILE_DIGEST,
      measurement_config_digest: MEASUREMENT_DIGEST,
      vm_image_digest: VM_IMAGE_DIGEST,
      destruction: "confirmed",
    });
    expect(storedReceipt).toMatchObject({
      binding: historicalProcessBindingInput(body),
      http_status: 200,
      body: terminalBody,
      destruction_state: "confirmed",
    });
    expect(destroyCalls).toBe(1);
    expect(logReads).toBe(1);

    const repeated = await handleReplayRequest(statusRequest(), enabled, dependencies);
    expect(repeated.status).toBe(200);
    expect(await repeated.json()).toEqual(terminalBody);
    expect(destroyCalls).toBe(1);
    expect(logReads).toBe(1);

    const startAfterTerminal = await handleReplayRequest(startRequest(), enabled, dependencies);
    expect(startAfterTerminal.status).toBe(202);
    expect(commands).toHaveLength(1);
  });

  it("treats a concurrent exact process-start duplicate as the same running handoff", async () => {
    const body = await historicalPublicInput();
    let claimedBinding: unknown = null;
    let initialReads = 0;
    let startCalls = 0;
    let destroyCalls = 0;
    let releaseInitialReads: (() => void) | undefined;
    const initialReadsComplete = new Promise<void>((resolve) => {
      releaseInitialReads = resolve;
    });
    const process = { getStatus: () => Promise.resolve("running") };
    const sandbox = {
      writeFile: (path: string) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
      exec: () => { throw new Error("blocking exec must remain unreachable"); },
      getProcess: async () => {
        if (initialReads < 2) {
          initialReads += 1;
          if (initialReads === 2) releaseInitialReads?.();
          await initialReadsComplete;
          return null;
        }
        return process as never;
      },
      startProcess: () => {
        startCalls += 1;
        if (startCalls === 1) return Promise.resolve(process as never);
        return Promise.reject(Object.assign(
          new Error("duplicate process"),
          { code: "PROCESS_ALREADY_EXISTS" },
        ));
      },
      destroy: () => {
        destroyCalls += 1;
        return Promise.resolve();
      },
    };
    const receipts = {
      readBinding: () => Promise.resolve(claimedBinding),
      claimBinding: (value: unknown) => {
        if (claimedBinding === null) claimedBinding = value;
        return Promise.resolve(claimedBinding);
      },
      readReceipt: () => Promise.resolve(null),
      prepareReceipt: (receipt: unknown) => Promise.resolve(receipt),
      confirmReceipt: () => Promise.reject(new Error("receipt is unavailable")),
    };
    const enabled = { ...REVIEWED_ENV, HISTORICAL_PUBLIC_REPLAY_ENABLED: "true" };
    const request = () => new Request(
      "https://example.test/api/v1/historical-public-replay",
      { method: "POST", body: JSON.stringify(body) },
    );
    const dependencies = {
      authenticate: () => Promise.resolve(),
      sandbox: () => sandbox,
      receiptStore: () => receipts,
    };

    const responses = await Promise.all([
      handleReplayRequest(request(), enabled, dependencies),
      handleReplayRequest(request(), enabled, dependencies),
    ]);
    expect(responses.map((response) => response.status)).toEqual([202, 202]);
    expect(await Promise.all(responses.map((response) => response.json()))).toEqual([
      {
        schema_version: 1,
        replay_task_id: body.replay_task_id,
        attempt: body.attempt,
        status: "running",
      },
      {
        schema_version: 1,
        replay_task_id: body.replay_task_id,
        attempt: body.attempt,
        status: "running",
      },
    ]);
    expect(startCalls).toBe(2);
    expect(initialReads).toBe(2);
    expect(destroyCalls).toBe(0);
  });

  it("only preserves the sandbox for an exact duplicate code with an ambiguous reread", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      const body = await historicalPublicInput();
      const cases = [
        {
          error: new Error("PROCESS_ALREADY_EXISTS"),
          expectedDestroyCalls: 1,
          rereadThrows: false,
        },
        {
          error: Object.assign(new Error("different failure"), { code: "DIFFERENT_ERROR" }),
          expectedDestroyCalls: 1,
          rereadThrows: false,
        },
        {
          error: Object.assign(new Error("duplicate process"), {
            code: "PROCESS_ALREADY_EXISTS",
          }),
          expectedDestroyCalls: 0,
          rereadThrows: false,
        },
        {
          error: Object.assign(new Error("duplicate process"), {
            code: "PROCESS_ALREADY_EXISTS",
          }),
          expectedDestroyCalls: 0,
          rereadThrows: true,
        },
      ];
      for (const testCase of cases) {
        let claimedBinding: unknown = null;
        let destroyCalls = 0;
        let processReads = 0;
        const response = await handleReplayRequest(new Request(
          "https://example.test/api/v1/historical-public-replay",
          { method: "POST", body: JSON.stringify(body) },
        ), { ...REVIEWED_ENV, HISTORICAL_PUBLIC_REPLAY_ENABLED: "true" }, {
          authenticate: () => Promise.resolve(),
          sandbox: () => ({
            writeFile: (path: string) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
            exec: () => { throw new Error("blocking exec must remain unreachable"); },
            getProcess: () => {
              processReads += 1;
              if (testCase.rereadThrows && processReads === 2) {
                return Promise.reject(new Error("process reread failed"));
              }
              return Promise.resolve(null);
            },
            startProcess: () => Promise.reject(testCase.error),
            destroy: () => {
              destroyCalls += 1;
              return Promise.resolve();
            },
          }),
          receiptStore: () => ({
            readBinding: () => Promise.resolve(claimedBinding),
            claimBinding: (value: unknown) => {
              if (claimedBinding === null) claimedBinding = value;
              return Promise.resolve(claimedBinding);
            },
            readReceipt: () => Promise.resolve(null),
            prepareReceipt: (receipt: unknown) => Promise.resolve(receipt),
            confirmReceipt: () => Promise.reject(new Error("receipt is unavailable")),
          }),
        });
        expect(response.status).toBe(500);
        expect(await response.json()).toEqual({
          error: "executor_failed",
          reason: "command_rpc_failed",
        });
        expect(destroyCalls).toBe(testCase.expectedDestroyCalls);
        expect(processReads).toBe(testCase.expectedDestroyCalls === 0 ? 2 : 1);
      }
    } finally {
      logged.mockRestore();
    }
  });

  it("rejects historical active-binding drift before sandbox lookup", async () => {
    const body = await historicalPublicInput();
    const activeBinding = activeBindingForTest(historicalProcessBindingInput(body));
    const status = historicalStatusInput(body);
    const mutations: [string, unknown][] = [
      ["runner_nonce", "a".repeat(64)],
      ["replay_task_id", `rt1_${"b".repeat(64)}`],
      ["attempt", 2],
      ["handoff_sha256", "c".repeat(64)],
      ["source_archive_sha256", "d".repeat(64)],
      ["execution_profile_digest", "e".repeat(64)],
      ["measurement_config_digest", "f".repeat(64)],
      ["vm_image_digest", `sha256:${"a".repeat(64)}`],
    ];
    for (const [field, value] of mutations) {
      let sandboxLookups = 0;
      const response = await handleReplayRequest(new Request(
        "https://example.test/api/v1/historical-public-replay/status",
        { method: "POST", body: JSON.stringify({ ...status, [field]: value }) },
      ), { ...REVIEWED_ENV, HISTORICAL_PUBLIC_REPLAY_ENABLED: "true" }, {
        authenticate: () => Promise.resolve(),
        sandbox: () => {
          sandboxLookups += 1;
          throw new Error("sandbox must remain unreachable");
        },
        receiptStore: () => ({
          readBinding: () => Promise.resolve(activeBinding),
          claimBinding: (claimed) => Promise.resolve(claimed),
          readReceipt: () => Promise.resolve(null),
          prepareReceipt: (receipt) => Promise.resolve(receipt),
          confirmReceipt: () => Promise.reject(new Error("receipt is unavailable")),
        }),
      });
      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ error: "invalid_request" });
      expect(sandboxLookups).toBe(0);
    }
  });

  it("rejects a historical duplicate start bound to another replay task", async () => {
    const body = await historicalPublicInput();
    const claimed = activeBindingForTest(historicalProcessBindingInput(body));
    const duplicate = { ...body, replay_task_id: `rt1_${"f".repeat(64)}` };
    let sandboxLookups = 0;
    const response = await handleReplayRequest(new Request(
      "https://example.test/api/v1/historical-public-replay",
      { method: "POST", body: JSON.stringify(duplicate) },
    ), { ...REVIEWED_ENV, HISTORICAL_PUBLIC_REPLAY_ENABLED: "true" }, {
      authenticate: () => Promise.resolve(),
      sandbox: () => {
        sandboxLookups += 1;
        throw new Error("sandbox must remain unreachable");
      },
      receiptStore: () => ({
        readBinding: () => Promise.resolve(claimed),
        claimBinding: () => Promise.resolve(claimed),
        readReceipt: () => Promise.resolve(null),
        prepareReceipt: (receipt) => Promise.resolve(receipt),
        confirmReceipt: () => Promise.reject(new Error("receipt is unavailable")),
      }),
    });
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: "invalid_request" });
    expect(sandboxLookups).toBe(0);
  });

  it("persists and replays an exact historical terminal failure after destruction", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      const body = await historicalPublicInput();
      const binding = historicalProcessBindingInput(body);
      const activeBinding = activeBindingForTest(binding);
      let receipt: unknown = null;
      let logReads = 0;
      let destroyCalls = 0;
      const receipts = {
        readBinding: () => Promise.resolve(activeBinding),
        claimBinding: () => Promise.resolve(activeBinding),
        readReceipt: () => Promise.resolve(receipt),
        prepareReceipt: (value: unknown) => {
          if (receipt === null) receipt = value;
          return Promise.resolve(receipt);
        },
        confirmReceipt: () => {
          receipt = { ...(receipt as Record<string, unknown>), destruction_state: "confirmed" };
          return Promise.resolve(receipt);
        },
      };
      const sandbox = {
        writeFile: (path: string) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
        exec: () => { throw new Error("blocking exec must remain unreachable"); },
        getProcess: () => Promise.resolve({
          getStatus: () => Promise.resolve("failed"),
          getLogs: () => {
            logReads += 1;
            return Promise.resolve({ stdout: "", stderr: "untrusted historical output" });
          },
        } as never),
        destroy: () => {
          destroyCalls += 1;
          return Promise.resolve();
        },
      };
      const request = () => new Request(
        "https://example.test/api/v1/historical-public-replay/status",
        { method: "POST", body: JSON.stringify(historicalStatusInput(body)) },
      );
      const enabled = { ...REVIEWED_ENV, HISTORICAL_PUBLIC_REPLAY_ENABLED: "true" };
      const dependencies = {
        authenticate: () => Promise.resolve(),
        sandbox: () => sandbox,
        receiptStore: () => receipts,
      };

      const first = await handleReplayRequest(request(), enabled, dependencies);
      expect(first.status).toBe(500);
      const failure = await first.json();
      expect(failure).toEqual({ error: "executor_failed", reason: "command_failed" });
      expect(JSON.stringify(failure)).not.toContain("untrusted historical output");
      expect(receipt).toMatchObject({
        binding,
        http_status: 500,
        body: failure,
        destruction_state: "confirmed",
      });
      expect(destroyCalls).toBe(1);
      expect(logReads).toBe(1);

      const repeated = await handleReplayRequest(request(), enabled, dependencies);
      expect(repeated.status).toBe(500);
      expect(await repeated.json()).toEqual(failure);
      expect(destroyCalls).toBe(1);
      expect(logReads).toBe(1);
      expect(logged).toHaveBeenCalledExactlyOnceWith(JSON.stringify({
        event: "lean_eval_replay_executor_failure",
        route: "historical_public_replay_status",
        reason: "command_failed",
      }));
      expect(logged.mock.calls.flat().join(" ")).not.toContain("untrusted historical output");
    } finally {
      logged.mockRestore();
    }
  });

  it("does not release a historical terminal verdict until destruction is confirmed", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      const body = await historicalPublicInput();
      const binding = historicalProcessBindingInput(body);
      const activeBinding = activeBindingForTest(binding);
      let receipt: unknown = null;
      let rejectDestruction = true;
      let destroyCalls = 0;
      let confirmCalls = 0;
      const receipts = {
        readBinding: () => Promise.resolve(activeBinding),
        claimBinding: () => Promise.resolve(activeBinding),
        readReceipt: () => Promise.resolve(receipt),
        prepareReceipt: (value: unknown) => {
          if (receipt === null) receipt = value;
          return Promise.resolve(receipt);
        },
        confirmReceipt: () => {
          confirmCalls += 1;
          receipt = { ...(receipt as Record<string, unknown>), destruction_state: "confirmed" };
          return Promise.resolve(receipt);
        },
      };
      const sandbox = {
        writeFile: (path: string) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
        exec: () => { throw new Error("blocking exec must remain unreachable"); },
        getProcess: () => Promise.resolve({
          getStatus: () => Promise.resolve("completed"),
          getLogs: () => Promise.resolve({
            stdout: JSON.stringify(historicalPublicRunnerVerdict(body)),
            stderr: "",
          }),
        } as never),
        destroy: () => {
          destroyCalls += 1;
          if (rejectDestruction) {
            rejectDestruction = false;
            return Promise.reject(new Error("destruction unavailable"));
          }
          return Promise.resolve();
        },
      };
      const request = () => new Request(
        "https://example.test/api/v1/historical-public-replay/status",
        { method: "POST", body: JSON.stringify(historicalStatusInput(body)) },
      );
      const dependencies = {
        authenticate: () => Promise.resolve(),
        sandbox: () => sandbox,
        receiptStore: () => receipts,
      };
      const enabled = { ...REVIEWED_ENV, HISTORICAL_PUBLIC_REPLAY_ENABLED: "true" };

      const first = await handleReplayRequest(request(), enabled, dependencies);
      expect(first.status).toBe(500);
      expect(await first.json()).toEqual({
        error: "executor_failed",
        reason: "sandbox_destroy_failed",
      });
      expect(receipt).toMatchObject({ destruction_state: "pending", http_status: 200 });
      expect(confirmCalls).toBe(0);

      const retry = await handleReplayRequest(request(), enabled, dependencies);
      expect(retry.status).toBe(200);
      expect(await retry.json()).toMatchObject({
        contract: "historical_public_executor_v1",
        destruction: "confirmed",
      });
      expect(destroyCalls).toBe(2);
      expect(confirmCalls).toBe(1);
    } finally {
      logged.mockRestore();
    }
  });

  it("fails closed on corrupt or differently bound historical receipts", async () => {
    const body = await historicalPublicInput();
    const binding = historicalProcessBindingInput(body);
    const activeBinding = activeBindingForTest(binding);
    const storedAt = 1_000;
    const terminalBody = {
      ...historicalStatusInput(body),
      contract: "historical_public_executor_v1",
      runner_verdict: historicalPublicRunnerVerdict(body),
      destruction: "confirmed",
    };
    const mismatched = {
      schema_version: 1,
      binding: { ...binding, source_archive_sha256: "f".repeat(64) },
      http_status: 200,
      body: terminalBody,
      destruction_state: "confirmed",
      stored_at_epoch_ms: storedAt,
      retained_until_epoch_ms: storedAt + 24 * 60 * 60 * 1000,
    };
    for (const stored of [{ schema_version: 1 }, mismatched]) {
      let processReads = 0;
      const response = await handleReplayRequest(new Request(
        "https://example.test/api/v1/historical-public-replay/status",
        { method: "POST", body: JSON.stringify(historicalStatusInput(body)) },
      ), { ...REVIEWED_ENV, HISTORICAL_PUBLIC_REPLAY_ENABLED: "true" }, {
        authenticate: () => Promise.resolve(),
        sandbox: () => ({
          writeFile: (path) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
          exec: () => { throw new Error("blocking exec must remain unreachable"); },
          getProcess: () => {
            processReads += 1;
            return Promise.resolve(null);
          },
          destroy: () => Promise.resolve(),
        }),
        receiptStore: () => ({
          readBinding: () => Promise.resolve(activeBinding),
          claimBinding: () => Promise.resolve(activeBinding),
          readReceipt: () => Promise.resolve(stored),
          prepareReceipt: (receipt) => Promise.resolve(receipt),
          confirmReceipt: () => Promise.resolve(stored),
        }),
      });
      expect(response.status).toBe(500);
      expect(await response.json()).toEqual({
        error: "executor_failed",
        reason: "command_output_invalid",
      });
      expect(processReads).toBe(0);
    }
  });

  it("starts one background command, polls it, and confirms destruction", async () => {
    const body = await authoritativeInput();
    const writes: string[] = [];
    const commands: string[] = [];
    const timeouts: number[] = [];
    let destroyed = false;
    let processStarted = false;
    let processStatus: "running" | "completed" = "running";
    const receipts = terminalReceiptStore();
    const process = {
      getStatus: () => Promise.resolve(processStatus),
      getLogs: () => Promise.resolve({
        stdout: JSON.stringify(acceptedVerdict(body)),
        stderr: "",
      }),
    };
    const sandbox = {
      writeFile: (path: string) => {
        writes.push(path);
        return Promise.resolve({ success: true, path, timestamp: "fixture" });
      },
      exec: () => { throw new Error("blocking exec must remain unreachable"); },
      getProcess: () => Promise.resolve(processStarted ? process as never : null),
      startProcess: (command: string, options?: { timeout?: number }) => {
        processStarted = true;
        commands.push(command);
        timeouts.push(options?.timeout ?? 0);
        return Promise.resolve(process as never);
      },
      destroy: () => {
        destroyed = true;
        return Promise.resolve();
      },
    };
    const start = await handleReplayRequest(new Request("https://example.test/api/v1/replay", {
      method: "POST",
      body: JSON.stringify(body),
    }), { ...REVIEWED_ENV, REPLAY_ENABLED: "true" }, {
      authenticate: () => Promise.resolve(),
      sandbox: () => sandbox,
      receiptStore: () => receipts,
    });
    expect(start.status).toBe(202);
    expect(await start.json()).toMatchObject({ status: "running" });
    expect(commands).toEqual(["/opt/lean-eval/replay-authoritative"]);
    expect(timeouts).toEqual([20_100_000]);
    expect(writes).toEqual([
      "/workspace/replay-request.json",
      "/workspace/archive-expectation.json",
      "/workspace/archive.tar.gz.age.b64",
      "/workspace/identity.age.b64",
    ]);
    expect(destroyed).toBe(false);

    const duplicateStart = await handleReplayRequest(new Request(
      "https://example.test/api/v1/replay",
      { method: "POST", body: JSON.stringify(body) },
    ), { ...REVIEWED_ENV, REPLAY_ENABLED: "true" }, {
      authenticate: () => Promise.resolve(),
      sandbox: () => sandbox,
      receiptStore: () => receipts,
    });
    expect(duplicateStart.status).toBe(202);
    expect(commands).toHaveLength(1);
    expect(writes).toHaveLength(4);

    const running = await handleReplayRequest(new Request(
      "https://example.test/api/v1/replay/status",
      { method: "POST", body: JSON.stringify(authoritativeStatusInput(body)) },
    ), REVIEWED_ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => sandbox,
      receiptStore: () => receipts,
    });
    expect(running.status).toBe(202);
    expect(await running.json()).toMatchObject({ status: "running" });
    expect(destroyed).toBe(false);

    processStatus = "completed";
    const status = await handleReplayRequest(new Request(
      "https://example.test/api/v1/replay/status",
      { method: "POST", body: JSON.stringify(authoritativeStatusInput(body)) },
    ), REVIEWED_ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => sandbox,
      receiptStore: () => receipts,
    });
    expect(status.status).toBe(200);
    const terminalBody = await status.json();
    expect(terminalBody).toMatchObject({ destruction: "confirmed" });
    expect(destroyed).toBe(true);

    const repeatedStatus = await handleReplayRequest(new Request(
      "https://example.test/api/v1/replay/status",
      { method: "POST", body: JSON.stringify(authoritativeStatusInput(body)) },
    ), REVIEWED_ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => sandbox,
      receiptStore: () => receipts,
    });
    expect(repeatedStatus.status).toBe(200);
    expect(await repeatedStatus.json()).toEqual(terminalBody);

    const startAfterLostTerminalResponse = await handleReplayRequest(new Request(
      "https://example.test/api/v1/replay",
      { method: "POST", body: JSON.stringify(body) },
    ), { ...REVIEWED_ENV, REPLAY_ENABLED: "true" }, {
      authenticate: () => Promise.resolve(),
      sandbox: () => sandbox,
      receiptStore: () => receipts,
    });
    expect(startAfterLostTerminalResponse.status).toBe(202);
    expect(commands).toHaveLength(1);
  });

  it("writes historical file-key material to its distinct sandbox input", async () => {
    const body = await authoritativeInput();
    body.schema_version = 2;
    delete body.plaintext_identity_base64;
    body.key_material_type = "age-file-key-v1";
    body.plaintext_key_material_base64 = btoa("0123456789abcdef");
    const expectation = body.archive_expectation as Record<string, unknown>;
    expectation.schema_version = 2;
    expectation.key_material_type = "age-file-key-v1";
    const writes: string[] = [];
    const response = await handleReplayRequest(new Request("https://example.test/api/v1/replay", {
      method: "POST",
      body: JSON.stringify(body),
    }), { ...REVIEWED_ENV, REPLAY_ENABLED: "true" }, {
      authenticate: () => Promise.resolve(),
      sandbox: () => ({
        writeFile: (path) => {
          writes.push(path);
          return Promise.resolve({ success: true, path, timestamp: "fixture" });
        },
        exec: () => { throw new Error("blocking exec must remain unreachable"); },
        getProcess: () => Promise.resolve(null),
        startProcess: () => Promise.resolve({
          getStatus: () => Promise.resolve("running" as const),
          getLogs: () => Promise.resolve({ stdout: "", stderr: "" }),
        } as never),
        destroy: () => Promise.resolve(),
      }),
      receiptStore: () => terminalReceiptStore(),
    });
    expect(response.status).toBe(202);
    expect(writes).toContain("/workspace/key-material.b64");
    expect(writes).not.toContain("/workspace/identity.age.b64");
  });

  it("persists the exact nonce binding before start and rejects a mismatched duplicate", async () => {
    const body = await authoritativeInput();
    const receipts = terminalReceiptStore();
    let sandboxLookups = 0;
    let starts = 0;
    const sandbox = {
      writeFile: (path: string) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
      exec: () => { throw new Error("blocking exec must remain unreachable"); },
      getProcess: () => Promise.resolve(starts === 0 ? null : {} as never),
      startProcess: () => {
        starts += 1;
        return Promise.resolve({} as never);
      },
      destroy: () => Promise.resolve(),
    };
    const dependencies = {
      authenticate: () => Promise.resolve(),
      sandbox: () => {
        sandboxLookups += 1;
        return sandbox;
      },
      receiptStore: () => receipts,
    };
    const first = await handleReplayRequest(new Request("https://example.test/api/v1/replay", {
      method: "POST",
      body: JSON.stringify(body),
    }), { ...REVIEWED_ENV, REPLAY_ENABLED: "true" }, dependencies);
    expect(first.status).toBe(202);

    const mismatched = structuredClone(body);
    (mismatched.request as Record<string, unknown>).replay_task_id = `rt1_${"9".repeat(64)}`;
    const duplicate = await handleReplayRequest(new Request("https://example.test/api/v1/replay", {
      method: "POST",
      body: JSON.stringify(mismatched),
    }), { ...REVIEWED_ENV, REPLAY_ENABLED: "true" }, dependencies);
    expect(duplicate.status).toBe(400);
    expect(await duplicate.json()).toEqual({ error: "invalid_request" });
    expect(sandboxLookups).toBe(1);
    expect(starts).toBe(1);
  });

  it("recovers a lost binding-claim response without starting an unbound process", async () => {
    const body = await authoritativeInput();
    let binding: unknown = null;
    let loseClaimResponse = true;
    let sandboxLookups = 0;
    let processStarted = false;
    const receipts = {
      readBinding: () => Promise.resolve(binding),
      claimBinding: (value: unknown) => {
        if (binding === null) binding = value;
        if (loseClaimResponse) {
          loseClaimResponse = false;
          return Promise.reject(new Error("lost binding claim response"));
        }
        return Promise.resolve(binding);
      },
      readReceipt: () => Promise.resolve(null),
      prepareReceipt: (value: unknown) => Promise.resolve(value),
      confirmReceipt: () => Promise.reject(new Error("receipt is unavailable")),
    };
    const dependencies = {
      authenticate: () => Promise.resolve(),
      sandbox: () => {
        sandboxLookups += 1;
        return {
          writeFile: (path: string) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
          exec: () => { throw new Error("blocking exec must remain unreachable"); },
          getProcess: () => Promise.resolve(processStarted ? {} as never : null),
          startProcess: () => {
            processStarted = true;
            return Promise.resolve({} as never);
          },
          destroy: () => Promise.resolve(),
        };
      },
      receiptStore: () => receipts,
    };
    const request = () => new Request("https://example.test/api/v1/replay", {
      method: "POST",
      body: JSON.stringify(body),
    });
    const first = await handleReplayRequest(
      request(),
      { ...REVIEWED_ENV, REPLAY_ENABLED: "true" },
      dependencies,
    );
    expect(first.status).toBe(500);
    expect(await first.json()).toEqual({
      error: "executor_failed",
      reason: "command_rpc_failed",
    });
    expect(sandboxLookups).toBe(0);

    const retry = await handleReplayRequest(
      request(),
      { ...REVIEWED_ENV, REPLAY_ENABLED: "true" },
      dependencies,
    );
    expect(retry.status).toBe(202);
    expect(sandboxLookups).toBe(1);
    expect(processStarted).toBe(true);
  });

  it("rejects missing, corrupt, or mismatched active bindings before sandbox lookup", async () => {
    const body = await authoritativeInput();
    const status = authoritativeStatusInput(body);
    const mismatched = activeBindingForTest({ ...status, attempt: 2 });
    for (const [binding, expectedStatus, reason] of [
      [null, 500, "command_rpc_failed"],
      [{ schema_version: 1 }, 500, "command_output_invalid"],
      [mismatched, 400, null],
    ] as const) {
      let sandboxLookups = 0;
      const response = await handleReplayRequest(new Request(
        "https://example.test/api/v1/replay/status",
        { method: "POST", body: JSON.stringify(status) },
      ), REVIEWED_ENV, {
        authenticate: () => Promise.resolve(),
        sandbox: () => {
          sandboxLookups += 1;
          throw new Error("sandbox must remain unreachable");
        },
        receiptStore: () => ({
          readBinding: () => Promise.resolve(binding),
          claimBinding: (value) => Promise.resolve(value),
          readReceipt: () => Promise.resolve(null),
          prepareReceipt: (value) => Promise.resolve(value),
          confirmReceipt: () => Promise.reject(new Error("receipt is unavailable")),
        }),
      });
      expect(response.status).toBe(expectedStatus);
      expect(await response.json()).toEqual(reason === null
        ? { error: "invalid_request" }
        : { error: "executor_failed", reason });
      expect(sandboxLookups).toBe(0);
    }
  });

  it("atomically selects one canonical terminal receipt across concurrent polls", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      const body = await authoritativeInput();
      const binding = activeBindingForTest(authoritativeStatusInput(body));
      let receipt: unknown = null;
      let logCalls = 0;
      let releaseLogs!: () => void;
      const logsReady = new Promise<void>((resolve) => { releaseLogs = resolve; });
      let prepareCalls = 0;
      let destroyCalls = 0;
      const receipts = {
        readBinding: () => Promise.resolve(binding),
        claimBinding: () => Promise.resolve(binding),
        readReceipt: () => Promise.resolve(receipt),
        prepareReceipt: (value: unknown) => {
          prepareCalls += 1;
          if (receipt === null) receipt = value;
          return Promise.resolve(receipt);
        },
        confirmReceipt: () => {
          if (
            typeof receipt === "object"
            && receipt !== null
            && !Array.isArray(receipt)
            && (receipt as Record<string, unknown>).destruction_state !== "confirmed"
          ) {
            receipt = { ...receipt, destruction_state: "confirmed" };
          }
          return Promise.resolve(receipt);
        },
      };
      const process = {
        getStatus: () => Promise.resolve("completed"),
        getLogs: async () => {
          logCalls += 1;
          const call = logCalls;
          if (call === 2) releaseLogs();
          await logsReady;
          return call === 1
            ? { stdout: JSON.stringify(acceptedVerdict(body)), stderr: "" }
            : { stdout: "not-json", stderr: "" };
        },
      };
      const sandbox = {
        writeFile: (path: string) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
        exec: () => { throw new Error("blocking exec must remain unreachable"); },
        getProcess: () => Promise.resolve(process as never),
        destroy: () => {
          destroyCalls += 1;
          return Promise.resolve();
        },
      };
      const request = () => new Request("https://example.test/api/v1/replay/status", {
        method: "POST",
        body: JSON.stringify(authoritativeStatusInput(body)),
      });
      const responses = await Promise.all([0, 1].map(() => handleReplayRequest(
        request(),
        REVIEWED_ENV,
        {
          authenticate: () => Promise.resolve(),
          sandbox: () => sandbox,
          receiptStore: () => receipts,
        },
      )));
      const responseBodies = await Promise.all(responses.map((response) => response.json()));
      expect(logCalls).toBe(2);
      expect(prepareCalls).toBe(2);
      expect(destroyCalls).toBe(2);
      expect(responses[0]?.status).toBe(responses[1]?.status);
      expect(responseBodies[0]).toEqual(responseBodies[1]);
      expect((receipt as Record<string, unknown>).destruction_state).toBe("confirmed");
    } finally {
      logged.mockRestore();
    }
  });

  it("leaves a running process intact when a status RPC is transiently unavailable", async () => {
    const body = await authoritativeInput();
    let destroyed = false;
    const receipts = terminalReceiptStore(authoritativeStatusInput(body));
    const response = await handleReplayRequest(new Request(
      "https://example.test/api/v1/replay/status",
      { method: "POST", body: JSON.stringify(authoritativeStatusInput(body)) },
    ), REVIEWED_ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => ({
        writeFile: (path) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
        exec: () => { throw new Error("blocking exec must remain unreachable"); },
        getProcess: () => Promise.reject(new Error("transient RPC failure")),
        destroy: () => {
          destroyed = true;
          return Promise.resolve();
        },
      }),
      receiptStore: () => receipts,
    });
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({
      error: "executor_failed",
      reason: "command_rpc_failed",
    });
    expect(destroyed).toBe(false);
  });

  it("retries a failed destruction from the durable pending receipt", async () => {
    const body = await authoritativeInput();
    const binding = activeBindingForTest(authoritativeStatusInput(body));
    let receipt: unknown = null;
    let destroyCalls = 0;
    const receipts = {
      readBinding: () => Promise.resolve(binding),
      claimBinding: () => Promise.resolve(binding),
      readReceipt: () => Promise.resolve(receipt),
      prepareReceipt: (value: unknown) => {
        if (receipt === null) receipt = value;
        return Promise.resolve(receipt);
      },
      confirmReceipt: () => {
        receipt = { ...(receipt as Record<string, unknown>), destruction_state: "confirmed" };
        return Promise.resolve(receipt);
      },
    };
    const sandbox = {
      writeFile: (path: string) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
      exec: () => { throw new Error("blocking exec must remain unreachable"); },
      getProcess: () => Promise.resolve({
        getStatus: () => Promise.resolve("completed"),
        getLogs: () => Promise.resolve({
          stdout: JSON.stringify(acceptedVerdict(body)),
          stderr: "",
        }),
      } as never),
      destroy: () => {
        destroyCalls += 1;
        return destroyCalls === 1
          ? Promise.reject(new Error("transient destroy failure"))
          : Promise.resolve();
      },
    };
    const request = () => new Request("https://example.test/api/v1/replay/status", {
      method: "POST",
      body: JSON.stringify(authoritativeStatusInput(body)),
    });
    const first = await handleReplayRequest(request(), REVIEWED_ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => sandbox,
      receiptStore: () => receipts,
    });
    expect(first.status).toBe(500);
    expect(await first.json()).toEqual({
      error: "executor_failed",
      reason: "sandbox_destroy_failed",
    });

    const retry = await handleReplayRequest(request(), REVIEWED_ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => sandbox,
      receiptStore: () => receipts,
    });
    expect(retry.status).toBe(200);
    expect(await retry.json()).toMatchObject({ destruction: "confirmed" });
    expect(destroyCalls).toBe(2);
  });

  it("recovers when the durable pending-receipt response is lost before destruction", async () => {
    const body = await authoritativeInput();
    const binding = activeBindingForTest(authoritativeStatusInput(body));
    let receipt: unknown = null;
    let rejectPrepared = true;
    let destroyCalls = 0;
    const receipts = {
      readBinding: () => Promise.resolve(binding),
      claimBinding: () => Promise.resolve(binding),
      readReceipt: () => Promise.resolve(receipt),
      prepareReceipt: (value: unknown) => {
        if (rejectPrepared) {
          rejectPrepared = false;
          receipt = value;
          return Promise.reject(new Error("lost pending receipt write response"));
        }
        if (receipt === null) receipt = value;
        return Promise.resolve(receipt);
      },
      confirmReceipt: () => {
        receipt = { ...(receipt as Record<string, unknown>), destruction_state: "confirmed" };
        return Promise.resolve(receipt);
      },
    };
    const sandbox = {
      writeFile: (path: string) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
      exec: () => { throw new Error("blocking exec must remain unreachable"); },
      getProcess: () => Promise.resolve({
        getStatus: () => Promise.resolve("completed"),
        getLogs: () => Promise.resolve({
          stdout: JSON.stringify(acceptedVerdict(body)),
          stderr: "",
        }),
      } as never),
      destroy: () => {
        destroyCalls += 1;
        return Promise.resolve();
      },
    };
    const request = () => new Request("https://example.test/api/v1/replay/status", {
      method: "POST",
      body: JSON.stringify(authoritativeStatusInput(body)),
    });
    const first = await handleReplayRequest(request(), REVIEWED_ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => sandbox,
      receiptStore: () => receipts,
    });
    expect(first.status).toBe(500);
    expect(await first.json()).toEqual({
      error: "executor_failed",
      reason: "command_rpc_failed",
    });
    expect(destroyCalls).toBe(0);

    const retry = await handleReplayRequest(request(), REVIEWED_ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => sandbox,
      receiptStore: () => receipts,
    });
    expect(retry.status).toBe(200);
    expect(await retry.json()).toMatchObject({ destruction: "confirmed" });
    expect(destroyCalls).toBe(1);
  });

  it("recovers after destruction when the durable confirmation response is lost", async () => {
    const body = await authoritativeInput();
    const binding = activeBindingForTest(authoritativeStatusInput(body));
    let receipt: unknown = null;
    let rejectConfirmation = true;
    let destroyCalls = 0;
    const receipts = {
      readBinding: () => Promise.resolve(binding),
      claimBinding: () => Promise.resolve(binding),
      readReceipt: () => Promise.resolve(receipt),
      prepareReceipt: (value: unknown) => {
        if (receipt === null) receipt = value;
        return Promise.resolve(receipt);
      },
      confirmReceipt: () => {
        if (rejectConfirmation) {
          rejectConfirmation = false;
          receipt = { ...(receipt as Record<string, unknown>), destruction_state: "confirmed" };
          return Promise.reject(new Error("lost receipt write response"));
        }
        receipt = { ...(receipt as Record<string, unknown>), destruction_state: "confirmed" };
        return Promise.resolve(receipt);
      },
    };
    const sandbox = {
      writeFile: (path: string) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
      exec: () => { throw new Error("blocking exec must remain unreachable"); },
      getProcess: () => Promise.resolve({
        getStatus: () => Promise.resolve("completed"),
        getLogs: () => Promise.resolve({
          stdout: JSON.stringify(acceptedVerdict(body)),
          stderr: "",
        }),
      } as never),
      destroy: () => {
        destroyCalls += 1;
        return Promise.resolve();
      },
    };
    const request = () => new Request("https://example.test/api/v1/replay/status", {
      method: "POST",
      body: JSON.stringify(authoritativeStatusInput(body)),
    });
    const first = await handleReplayRequest(request(), REVIEWED_ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => sandbox,
      receiptStore: () => receipts,
    });
    expect(first.status).toBe(500);
    expect(await first.json()).toEqual({
      error: "executor_failed",
      reason: "command_rpc_failed",
    });

    const retry = await handleReplayRequest(request(), REVIEWED_ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => sandbox,
      receiptStore: () => receipts,
    });
    expect(retry.status).toBe(200);
    expect(await retry.json()).toMatchObject({ destruction: "confirmed" });
    expect(destroyCalls).toBe(1);
  });

  it("fails closed on corrupt or differently bound terminal receipts", async () => {
    const body = await authoritativeInput();
    const binding = authoritativeStatusInput(body);
    const storedAt = 1_000;
    const mismatched = {
      schema_version: 1,
      binding: { ...binding, runner_nonce: "9".repeat(64) },
      http_status: 200,
      body: {
        schema_version: 1,
        verdict: acceptedVerdict(body),
        destruction: "confirmed",
      },
      destruction_state: "confirmed",
      stored_at_epoch_ms: storedAt,
      retained_until_epoch_ms: storedAt + 24 * 60 * 60 * 1000,
    };
    for (const receipt of [{ schema_version: 1 }, mismatched]) {
      let processRead = false;
      const response = await handleReplayRequest(new Request(
        "https://example.test/api/v1/replay/status",
        { method: "POST", body: JSON.stringify(binding) },
      ), REVIEWED_ENV, {
        authenticate: () => Promise.resolve(),
        sandbox: () => ({
          writeFile: (path) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
          exec: () => { throw new Error("blocking exec must remain unreachable"); },
          getProcess: () => {
            processRead = true;
            return Promise.resolve(null);
          },
          destroy: () => Promise.resolve(),
        }),
        receiptStore: () => ({
          readBinding: () => Promise.resolve(activeBindingForTest(binding)),
          claimBinding: (value) => Promise.resolve(value),
          readReceipt: () => Promise.resolve(receipt),
          prepareReceipt: (value) => Promise.resolve(value),
          confirmReceipt: () => Promise.resolve(receipt),
        }),
      });
      expect(response.status).toBe(500);
      expect(await response.json()).toEqual({
        error: "executor_failed",
        reason: "command_output_invalid",
      });
      expect(processRead).toBe(false);
    }
  });

  it("destroys without executing if authoritative input transfer is not confirmed", async () => {
    let executed = false;
    let destroyed = false;
    const receipts = terminalReceiptStore();
    const response = await handleReplayRequest(new Request("https://example.test/api/v1/replay", {
      method: "POST",
      body: JSON.stringify(await authoritativeInput()),
    }), { ...REVIEWED_ENV, REPLAY_ENABLED: "true" }, {
      authenticate: () => Promise.resolve(),
      sandbox: () => ({
        writeFile: (path) => Promise.resolve({ success: false, path, timestamp: "fixture" }),
        exec: () => { throw new Error("blocking exec must remain unreachable"); },
        getProcess: () => Promise.resolve(null),
        startProcess: () => {
          executed = true;
          throw new Error("start must remain unreachable");
        },
        destroy: () => {
          destroyed = true;
          return Promise.resolve();
        },
      }),
      receiptStore: () => receipts,
    });
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({
      error: "executor_failed",
      reason: "input_transfer_failed",
    });
    expect(executed).toBe(false);
    expect(destroyed).toBe(true);
  });

  it("returns and logs only an allowlisted background failure classification", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      const body = await authoritativeInput();
      const receipts = terminalReceiptStore(authoritativeStatusInput(body));
      const sandbox = {
        writeFile: (path: string) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
        exec: () => { throw new Error("blocking exec must remain unreachable"); },
        getProcess: () => Promise.resolve({
          getStatus: () => Promise.resolve("failed"),
          getLogs: () => Promise.resolve({
            stdout: "",
            stderr: "replay-authoritative: measurement evidence is unavailable\n",
          }),
        } as never),
        destroy: () => Promise.resolve(),
      };
      const response = await handleReplayRequest(new Request(
        "https://example.test/api/v1/replay/status",
        { method: "POST", body: JSON.stringify(authoritativeStatusInput(body)) },
      ), REVIEWED_ENV, {
        authenticate: () => Promise.resolve(),
        sandbox: () => sandbox,
        receiptStore: () => receipts,
      });
      expect(response.status).toBe(500);
      const responseBody = await response.json();
      expect(responseBody).toEqual({
        error: "executor_failed",
        reason: "command_failed",
        detail: "measurement_evidence_unavailable",
      });
      expect(logged).toHaveBeenCalledExactlyOnceWith(JSON.stringify({
        event: "lean_eval_replay_executor_failure",
        route: "authoritative_replay_status",
        reason: "command_failed",
        detail: "measurement_evidence_unavailable",
      }));

      const repeated = await handleReplayRequest(new Request(
        "https://example.test/api/v1/replay/status",
        { method: "POST", body: JSON.stringify(authoritativeStatusInput(body)) },
      ), REVIEWED_ENV, {
        authenticate: () => Promise.resolve(),
        sandbox: () => sandbox,
        receiptStore: () => receipts,
      });
      expect(repeated.status).toBe(500);
      expect(await repeated.json()).toEqual(responseBody);
      expect(logged).toHaveBeenCalledTimes(1);
    } finally {
      logged.mockRestore();
    }
  });

  it("classifies evaluator preflight failures without exposing command output", async () => {
    const body = await authoritativeInput();
    const receipts = terminalReceiptStore(authoritativeStatusInput(body));
    const response = await handleReplayRequest(new Request(
      "https://example.test/api/v1/replay/status",
      { method: "POST", body: JSON.stringify(authoritativeStatusInput(body)) },
    ), REVIEWED_ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => ({
        writeFile: (path) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
        exec: () => { throw new Error("blocking exec must remain unreachable"); },
        getProcess: () => Promise.resolve({
          getStatus: () => Promise.resolve("failed"),
          getLogs: () => Promise.resolve({
            stdout: "",
            stderr: "replay-authoritative: evaluator failed before measurement\n",
          }),
        } as never),
        destroy: () => Promise.resolve(),
      }),
      receiptStore: () => receipts,
    });
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({
      error: "executor_failed",
      reason: "command_failed",
      detail: "evaluator_preflight_failed",
    });
  });

  it("does not expose unclassified authoritative stderr", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const sensitive = "private identity fixture";
    try {
      const body = await authoritativeInput();
      const receipts = terminalReceiptStore(authoritativeStatusInput(body));
      const response = await handleReplayRequest(new Request(
        "https://example.test/api/v1/replay/status",
        { method: "POST", body: JSON.stringify(authoritativeStatusInput(body)) },
      ), REVIEWED_ENV, {
        authenticate: () => Promise.resolve(),
        sandbox: () => ({
          writeFile: (path) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
          exec: () => { throw new Error("blocking exec must remain unreachable"); },
          getProcess: () => Promise.resolve({
            getStatus: () => Promise.resolve("failed"),
            getLogs: () => Promise.resolve({
              stdout: "",
              stderr: `replay-authoritative: ${sensitive}\n`,
            }),
          } as never),
          destroy: () => Promise.resolve(),
        }),
        receiptStore: () => receipts,
      });
      const responseBody = await response.json();
      expect(response.status).toBe(500);
      expect(responseBody).toEqual({
        error: "executor_failed",
        reason: "command_failed",
        detail: "unclassified_authoritative_failure",
      });
      expect(JSON.stringify(responseBody)).not.toContain(sensitive);
      expect(logged).toHaveBeenCalledExactlyOnceWith(JSON.stringify({
        event: "lean_eval_replay_executor_failure",
        route: "authoritative_replay_status",
        reason: "command_failed",
        detail: "unclassified_authoritative_failure",
      }));
      expect(logged.mock.calls.flat().join(" ")).not.toContain(sensitive);
    } finally {
      logged.mockRestore();
    }
  });

  it("keeps production execution disabled in public health", async () => {
    const response = await handleReplayRequest(new Request("https://example.test/healthz"), ENV);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      replay_enabled: false,
      historical_public_replay_enabled: false,
      staging_acceptance_enabled: true,
      staging_memory_limit_bytes: 12_884_901_888,
      production_memory_gate_bytes: 12_884_901_888,
      reviewed_execution_profile_digest: "0".repeat(64),
      reviewed_measurement_config_digest: "0".repeat(64),
      reviewed_vm_image_digest: `sha256:${"0".repeat(64)}`,
    });
  });

  it("runs a fixed command and confirms destruction before returning evidence", async () => {
    const body = await input();
    const writes: string[] = [];
    const commands: string[] = [];
    let destroyed = false;
    const response = await handleReplayRequest(new Request("https://example.test/api/v1/staging-acceptance", {
      method: "POST",
      body: JSON.stringify(body),
    }), ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => ({
        writeFile: (path) => {
          writes.push(path);
          return Promise.resolve({ success: true, path, timestamp: "fixture" });
        },
        exec: (command) => {
          commands.push(command);
          return Promise.resolve({
            success: true,
            exitCode: 0,
            stdout: JSON.stringify({
              schema_version: 1,
              archive_ciphertext_sha256: body.archive_ciphertext_sha256,
              marker_sha256: body.marker_sha256,
              network_probe: "blocked",
              architecture: "x86_64",
              kernel_release: "fixture-kernel",
              cpu_model: "fixture-cpu",
            }),
            stderr: "",
            command,
            duration: 1,
            timestamp: "fixture",
          });
        },
        destroy: () => {
          destroyed = true;
          return Promise.resolve();
        },
      }),
    });
    expect(response.status).toBe(200);
    expect(commands).toEqual(["/opt/lean-eval/replay-staging-acceptance"]);
    expect(writes).toEqual([
      "/workspace/archive.tar.gz.age.b64",
      "/workspace/identity.age.b64",
      "/workspace/expectation.json",
    ]);
    expect(destroyed).toBe(true);
    expect(await response.json()).toMatchObject({ destruction: "confirmed", network_policy: "disabled" });
  });

  it("attests one accepted archive with a separate fixed command", async () => {
    const body = await archiveInput();
    const writes: string[] = [];
    const commands: string[] = [];
    let destroyed = false;
    const response = await handleReplayRequest(new Request(
      "https://example.test/api/v1/staging-archive-acceptance",
      { method: "POST", body: JSON.stringify(body) },
    ), ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => ({
        writeFile: (path) => {
          writes.push(path);
          return Promise.resolve({ success: true, path, timestamp: "fixture" });
        },
        exec: (command) => {
          commands.push(command);
          return Promise.resolve({
            success: true,
            exitCode: 0,
            stdout: JSON.stringify({
              schema_version: 1,
              submission_id: body.submission_id,
              archive_ciphertext_sha256: body.archive_ciphertext_sha256,
              plaintext_tar_sha256: body.plaintext_tar_sha256,
              plaintext_tar_size: body.plaintext_tar_size,
              network_probe: "blocked",
              architecture: "x86_64",
              kernel_release: "fixture-kernel",
              cpu_model: "fixture-cpu",
            }),
            stderr: "",
            command,
            duration: 1,
            timestamp: "fixture",
          });
        },
        destroy: () => {
          destroyed = true;
          return Promise.resolve();
        },
      }),
    });
    expect(response.status).toBe(200);
    expect(commands).toEqual(["/opt/lean-eval/replay-archive-acceptance"]);
    expect(writes).toEqual([
      "/workspace/archive.tar.gz.age.b64",
      "/workspace/identity.age.b64",
      "/workspace/archive-expectation.json",
    ]);
    expect(destroyed).toBe(true);
    expect(await response.json()).toMatchObject({
      submission_id: body.submission_id,
      destruction: "confirmed",
      network_policy: "disabled",
    });
  });

  it("logs only an allowlisted archive command failure classification", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      const response = await handleReplayRequest(new Request(
        "https://example.test/api/v1/staging-archive-acceptance",
        { method: "POST", body: JSON.stringify(await archiveInput()) },
      ), ENV, {
        authenticate: () => Promise.resolve(),
        sandbox: () => ({
          writeFile: (path) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
          exec: (command) => Promise.resolve({
            success: false,
            exitCode: 1,
            stdout: "",
            stderr: "archive decryption failed\n",
            command,
            duration: 1,
            timestamp: "fixture",
          }),
          destroy: () => Promise.resolve(),
        }),
      });
      expect(response.status).toBe(500);
      expect(await response.json()).toEqual({
        error: "executor_failed",
        reason: "command_failed",
        detail: "archive_decryption_failed",
      });
      expect(logged).toHaveBeenCalledExactlyOnceWith(JSON.stringify({
        event: "lean_eval_replay_executor_failure",
        route: "archive_acceptance",
        reason: "command_failed",
        detail: "archive_decryption_failed",
      }));
    } finally {
      logged.mockRestore();
    }
  });

  it("does not expose unclassified archive command output", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const sensitive = "private identity fixture";
    try {
      const response = await handleReplayRequest(new Request(
        "https://example.test/api/v1/staging-archive-acceptance",
        { method: "POST", body: JSON.stringify(await archiveInput()) },
      ), ENV, {
        authenticate: () => Promise.resolve(),
        sandbox: () => ({
          writeFile: (path) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
          exec: (command) => Promise.resolve({
            success: false,
            exitCode: 1,
            stdout: "",
            stderr: sensitive,
            command,
            duration: 1,
            timestamp: "fixture",
          }),
          destroy: () => Promise.resolve(),
        }),
      });
      const responseBody = await response.json();
      expect(response.status).toBe(500);
      expect(responseBody).toEqual({
        error: "executor_failed",
        reason: "command_failed",
        detail: "unclassified_archive_failure",
      });
      expect(JSON.stringify(responseBody)).not.toContain(sensitive);
      expect(logged.mock.calls.flat().join(" ")).not.toContain(sensitive);
    } finally {
      logged.mockRestore();
    }
  });

  it("destroys the sandbox on execution failure without exposing diagnostics", async () => {
    let destroyed = false;
    const response = await handleReplayRequest(new Request("https://example.test/api/v1/staging-acceptance", {
      method: "POST",
      body: JSON.stringify(await input()),
    }), ENV, {
      authenticate: () => Promise.resolve(),
      sandbox: () => ({
        writeFile: (path) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
        exec: () => Promise.reject(new Error("private identity fixture")),
        destroy: () => {
          destroyed = true;
          return Promise.resolve();
        },
      }),
    });
    expect(response.status).toBe(500);
    const responseBody = await response.json();
    expect(responseBody).toEqual({
      error: "executor_failed",
      reason: "command_rpc_failed",
    });
    expect(JSON.stringify(responseBody)).not.toContain("private identity fixture");
    expect(destroyed).toBe(true);
  });

  it("recovers a cancellation immediately after 202 with idempotent source-free cleanup", async () => {
    const body = await historicalPublicInput();
    let activeBinding: unknown = null;
    let cleanupMarker: unknown = null;
    let cleanupReservation: unknown = null;
    let cleanupDestroyCalls = 0;
    let cleanupRequests = 0;
    const receipts = {
      readBinding: () => Promise.resolve(activeBinding),
      claimBinding: (value: unknown) => {
        if (activeBinding === null) activeBinding = value;
        return Promise.resolve(activeBinding);
      },
      readReceipt: () => Promise.resolve(null),
      prepareReceipt: (value: unknown) => Promise.resolve(value),
      confirmReceipt: () => Promise.reject(new Error("receipt is unavailable")),
    };
    const enabled = { ...REVIEWED_ENV, HISTORICAL_PUBLIC_REPLAY_ENABLED: "true" };
    const dependencies = {
      authenticate: () => Promise.resolve(),
      sandbox: () => ({
        writeFile: (path: string) => Promise.resolve({ success: true, path, timestamp: "fixture" }),
        exec: () => { throw new Error("blocking exec must remain unreachable"); },
        getProcess: () => Promise.resolve(null),
        startProcess: () => Promise.resolve({ getStatus: () => Promise.resolve("running") } as never),
        destroy: () => Promise.reject(new Error("request path must not clean up the winner")),
      }),
      receiptStore: () => receipts,
      recoveryStore: (_env: ReplayRuntimeEnv, replayTaskId: string, attempt: number) => ({
        reserveCleanupIdentity: (expected: unknown) => {
          if (cleanupReservation === null) cleanupReservation = expected;
          return Promise.resolve(cleanupReservation);
        },
        destroyBoundSandbox: (expected: { replay_task_id: string; attempt: number }) => {
          cleanupRequests += 1;
          expect(activeBinding).toMatchObject({
            runner_nonce: body.runner_nonce,
            replay_task_id: replayTaskId,
            attempt,
          });
          expect(expected).toEqual({
            schema_version: 1,
            replay_task_id: replayTaskId,
            attempt,
          });
          if (cleanupMarker === null) {
            cleanupDestroyCalls += 1;
            cleanupMarker = {
              ...expected,
              destruction_state: "confirmed",
              confirmed_at_epoch_ms: 1_000,
              retained_until_epoch_ms: 2_000,
            };
          }
          return Promise.resolve(cleanupRequests === 1 ? cleanupMarker : {
            ...expected,
            destruction_state: "confirmed",
          });
        },
      }),
    };
    const cleanupBody = {
      schema_version: 1,
      replay_task_id: body.replay_task_id,
      attempt: body.attempt,
    };
    const reservation = await handleReplayRequest(new Request(
      "https://example.test/api/v1/historical-public-replay/cleanup-reservation",
      { method: "POST", body: JSON.stringify(cleanupBody) },
    ), enabled, dependencies);
    expect(reservation.status).toBe(200);
    expect(await reservation.json()).toEqual({ ...cleanupBody, status: "reserved" });
    const start = await handleReplayRequest(new Request(
      "https://example.test/api/v1/historical-public-replay",
      { method: "POST", body: JSON.stringify(body) },
    ), enabled, dependencies);
    expect(start.status).toBe(202);

    for (let index = 0; index < 2; index += 1) {
      const cleanup = await handleReplayRequest(new Request(
        "https://example.test/api/v1/historical-public-replay/cleanup",
        { method: "POST", body: JSON.stringify(cleanupBody) },
      ), enabled, dependencies);
      expect(cleanup.status).toBe(200);
      expect(await cleanup.json()).toEqual({ ...cleanupBody, destruction: "confirmed" });
    }
    expect(cleanupDestroyCalls).toBe(1);
    expect(JSON.stringify(cleanupBody)).not.toContain(body.runner_nonce as string);
    expect(JSON.stringify(cleanupBody)).not.toContain("source_archive");
  });

  it("recovers cancellation after replay.started but before an executor binding exists", async () => {
    const body = await historicalPublicInput();
    const identity = {
      schema_version: 1,
      replay_task_id: body.replay_task_id,
      attempt: body.attempt,
    };
    let reservation: unknown = null;
    let marker: unknown = null;
    let sandboxLookups = 0;
    const enabled = { ...REVIEWED_ENV, HISTORICAL_PUBLIC_REPLAY_ENABLED: "true" };
    const dependencies = {
      authenticate: () => Promise.resolve(),
      sandbox: () => {
        sandboxLookups += 1;
        throw new Error("pre-binding cleanup must not look up a sandbox");
      },
      recoveryStore: () => ({
        reserveCleanupIdentity: (expected: unknown) => {
          if (reservation === null) reservation = expected;
          return Promise.resolve(reservation);
        },
        destroyBoundSandbox: (expected: unknown) => {
          expect(reservation).toEqual(expected);
          if (marker === null) {
            marker = {
              ...(expected as Record<string, unknown>),
              destruction_state: "confirmed",
              confirmed_at_epoch_ms: 1_000,
              retained_until_epoch_ms: 2_000,
            };
          }
          return Promise.resolve(marker);
        },
      }),
    };

    const reserved = await handleReplayRequest(new Request(
      "https://example.test/api/v1/historical-public-replay/cleanup-reservation",
      { method: "POST", body: JSON.stringify(identity) },
    ), enabled, dependencies);
    expect(reserved.status).toBe(200);
    expect(await reserved.json()).toEqual({ ...identity, status: "reserved" });

    const cleanup = await handleReplayRequest(new Request(
      "https://example.test/api/v1/historical-public-replay/cleanup",
      { method: "POST", body: JSON.stringify(identity) },
    ), enabled, dependencies);
    expect(cleanup.status).toBe(200);
    expect(await cleanup.json()).toEqual({ ...identity, destruction: "confirmed" });
    expect(sandboxLookups).toBe(0);
    expect(JSON.stringify(marker)).not.toContain(body.runner_nonce as string);
    expect(JSON.stringify(marker)).not.toContain("source_archive");
  });

  it("rejects a durable cleanup confirmation with mismatched identity", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      const body = {
        schema_version: 1,
        replay_task_id: `rt1_${"2".repeat(64)}`,
        attempt: 1,
      };
      let sandboxLookups = 0;
      const response = await handleReplayRequest(new Request(
        "https://example.test/api/v1/historical-public-replay/cleanup",
        { method: "POST", body: JSON.stringify(body) },
      ), { ...REVIEWED_ENV, HISTORICAL_PUBLIC_REPLAY_ENABLED: "true" }, {
        authenticate: () => Promise.resolve(),
        sandbox: () => {
          sandboxLookups += 1;
          throw new Error("sandbox must remain unreachable from the route");
        },
        recoveryStore: () => ({
          reserveCleanupIdentity: (expected: unknown) => Promise.resolve(expected),
          destroyBoundSandbox: () => Promise.resolve({
            ...body,
            attempt: 2,
            destruction_state: "confirmed",
            confirmed_at_epoch_ms: 1_000,
            retained_until_epoch_ms: 2_000,
          }),
        }),
      });
      expect(response.status).toBe(500);
      expect(await response.json()).toEqual({
        error: "executor_failed",
        reason: "command_output_invalid",
      });
      expect(sandboxLookups).toBe(0);
    } finally {
      logged.mockRestore();
    }
  });
});
