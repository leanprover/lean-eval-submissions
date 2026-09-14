import { describe, expect, it, vi } from "vitest";

import { handleReplayRequest, type ReplayRuntimeEnv } from "../src/replay-app";

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

const ACCEPTED_ARCHIVE = "age-encryption.org/v1\naccepted-fixture";

async function archiveInput(): Promise<Record<string, unknown>> {
  return {
    schema_version: 1,
    request_id: "0198c4ee-7d2d-7b35-8d20-cd5db8aa9a6f",
    runner_nonce: "1".repeat(64),
    submission_id: "01a02cb4-5e7c-7fb3-a4ab-b6fabbb72584",
    archive_ciphertext_sha256: await hexDigest(new TextEncoder().encode(ACCEPTED_ARCHIVE)),
    plaintext_tar_sha256: "2".repeat(64),
    plaintext_tar_size: 712,
    archive_ciphertext_bytes: ACCEPTED_ARCHIVE.length,
    archive_part_count: 1,
    plaintext_identity_base64: btoa("AGE-SECRET-KEY-1FIXTURE"),
  };
}

const FIXTURE_ARCHIVE = "age-encryption.org/v1\nauthoritative-fixture";

async function hexDigest(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

const FIXTURE_ARCHIVE_DIGEST = await hexDigest(new TextEncoder().encode(FIXTURE_ARCHIVE));

/** The upload record a start expects to find: complete and assembled. */
function assembledUploadForTest(
  archiveSha256: string = FIXTURE_ARCHIVE_DIGEST,
  archiveBytes: number = FIXTURE_ARCHIVE.length,
  partCount = 1,
  uploadKind = "authoritative-archive",
): Record<string, unknown> {
  return {
    schema_version: 1,
    upload_kind: uploadKind,
    runner_nonce: "1".repeat(64),
    archive_sha256: archiveSha256,
    archive_bytes: archiveBytes,
    part_count: partCount,
    parts: [],
    assembled_path: "/workspace/archive.tar.gz.age",
  };
}

/** The assembled upload matching an `authoritativeInput` body. */
function uploadForBody(body: Record<string, unknown>): Record<string, unknown> {
  const expectation = body.archive_expectation as Record<string, unknown>;
  return assembledUploadForTest(
    expectation.archive_ciphertext_sha256 as string,
    body.archive_ciphertext_bytes as number,
    body.archive_part_count as number,
  );
}

async function authoritativeInput(
  ciphertextContents = FIXTURE_ARCHIVE,
): Promise<Record<string, unknown>> {
  const bytes = new TextEncoder().encode(ciphertextContents);
  const archiveDigest = await hexDigest(bytes);
  const submissionId = "01a02cb4-5e7c-7fb3-a4ab-b6fabbb72584";
  return {
    schema_version: 3,
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
    archive_ciphertext_bytes: ciphertextContents.length,
    archive_part_count: 1,
    key_material_type: "age-identity-v1",
    plaintext_key_material_base64: btoa("AGE-SECRET-KEY-1FIXTURE"),
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

/**
 * In-memory stand-in for the receipt Durable Object.
 *
 * `archiveUpload` starts out already assembled so the many tests that only care
 * about start, status and destruction do not each have to stage an upload; the
 * upload-specific tests pass their own state explicitly.
 */
function terminalReceiptStore(
  initialBinding?: Record<string, unknown>,
  initialUpload?: Record<string, unknown> | null,
) {
  let binding: unknown = initialBinding === undefined
    ? null
    : activeBindingForTest(initialBinding);
  let receipt: unknown = null;
  let upload: unknown = initialUpload === undefined
    ? assembledUploadForTest()
    : initialUpload;
  return {
    readArchiveUpload: () => Promise.resolve(upload),
    claimArchiveUpload: (value: unknown) => {
      // First-writer-wins on the identity, like the Durable Object.
      if (upload === null) upload = { ...(value as object), parts: [], assembled_path: null };
      const stored = upload as Record<string, unknown>;
      const wanted = value as Record<string, unknown>;
      if (stored.archive_sha256 !== wanted.archive_sha256) {
        return Promise.reject(new Error("runner nonce is already bound to a different archive upload"));
      }
      return Promise.resolve(upload);
    },
    commitArchiveUploadPart: (_identity: unknown, part: unknown) => {
      const current = upload as { parts: unknown[] } | null;
      if (current === null) return Promise.reject(new Error("archive upload was not claimed"));
      current.parts = [...current.parts, part];
      return Promise.resolve(current);
    },
    finalizeArchiveUpload: (_identity: unknown, assembledPath: string) => {
      const current = upload as Record<string, unknown> | null;
      if (current === null) return Promise.reject(new Error("archive upload was not claimed"));
      upload = { ...current, assembled_path: assembledPath };
      return Promise.resolve(upload);
    },
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


/** Store fake for the staging archive acceptance route's assembled upload. */
function acceptedArchiveReceipts(body: Record<string, unknown>) {
  return terminalReceiptStore(undefined, assembledUploadForTest(
    body.archive_ciphertext_sha256 as string,
    body.archive_ciphertext_bytes as number,
    body.archive_part_count as number,
    "staging-archive-acceptance",
  ));
}

const ENV = {
  DEPLOYED_COMMIT: "a".repeat(40),
  DEPLOYMENT_ENVIRONMENT: "staging",
  REPLAY_ENABLED: "false",
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

  it("starts one background command, polls it, and confirms destruction", async () => {
    const body = await authoritativeInput(
      `age-encryption.org/v1\n${"a".repeat(1_500_000)}`,
    );
    const writes = new Map<string, string | ReadableStream<Uint8Array>>();
    const commands: string[] = [];
    const timeouts: number[] = [];
    let destroyed = false;
    let processStarted = false;
    let processStatus: "running" | "completed" = "running";
    const receipts = terminalReceiptStore(undefined, uploadForBody(body));
    const process = {
      getStatus: () => Promise.resolve(processStatus),
      getLogs: () => Promise.resolve({
        stdout: JSON.stringify(acceptedVerdict(body)),
        stderr: "",
      }),
    };
    const sandbox = {
      writeFile: (path: string, contents: string | ReadableStream<Uint8Array>) => {
        writes.set(path, contents);
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
    // The archive is not written here any more: it was uploaded in parts and
    // assembled before this request, so a start writes only the small inputs.
    expect([...writes.keys()]).toEqual([
      "/workspace/replay-request.json",
      "/workspace/archive-expectation.json",
      "/workspace/identity.age.b64",
    ]);
    expect(writes.get("/workspace/identity.age.b64"))
      .toBe(body.plaintext_key_material_base64);
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
    expect(writes.size).toBe(3);

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

  it("does not issue a sandbox RPC before asynchronous sandbox setup completes", async () => {
    const body = await authoritativeInput();
    let rpcCalls = 0;
    const sandbox = {
      writeFile: (path: string) => {
        rpcCalls += 1;
        return Promise.resolve({ success: true, path, timestamp: "fixture" });
      },
      exec: () => { throw new Error("blocking exec must remain unreachable"); },
      getProcess: () => {
        rpcCalls += 1;
        return Promise.resolve(null);
      },
      startProcess: () => {
        rpcCalls += 1;
        return Promise.resolve({} as never);
      },
      destroy: () => Promise.resolve(),
    };
    let releaseSandbox: (value: typeof sandbox) => void = () => {
      throw new Error("sandbox release was not initialized");
    };
    let markRequested: () => void = () => {
      throw new Error("sandbox request marker was not initialized");
    };
    const sandboxReady = new Promise<typeof sandbox>((resolve) => {
      releaseSandbox = resolve;
    });
    const sandboxRequested = new Promise<void>((resolve) => {
      markRequested = resolve;
    });

    const responsePromise = handleReplayRequest(new Request(
      "https://example.test/api/v1/replay",
      { method: "POST", body: JSON.stringify(body) },
    ), { ...REVIEWED_ENV, REPLAY_ENABLED: "true" }, {
      authenticate: () => Promise.resolve(),
      sandbox: () => {
        markRequested();
        return sandboxReady;
      },
      receiptStore: () => terminalReceiptStore(),
    });

    await sandboxRequested;
    expect(rpcCalls).toBe(0);
    releaseSandbox(sandbox);
    const response = await responsePromise;
    expect(response.status).toBe(202);
    expect(rpcCalls).toBeGreaterThan(0);
  });

  it("writes file-key material to its distinct sandbox input", async () => {
    const body = await authoritativeInput();
    // The transport version stays 3; only the archive envelope moves to v2,
    // which is what selects the file-key form.
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
      receiptStore: () => terminalReceiptStore(undefined, uploadForBody(body)),
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
      // The archive was uploaded and assembled before this start; only the
      // binding claim is being made lossy here.
      readArchiveUpload: () => Promise.resolve(uploadForBody(body)),
      claimArchiveUpload: (value: unknown) => Promise.resolve(value),
      commitArchiveUploadPart: (_identity: unknown, part: unknown) => Promise.resolve(part),
      finalizeArchiveUpload: (_identity: unknown, path: string) => Promise.resolve(path),
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
      receiptStore: () => acceptedArchiveReceipts(body),
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
    // The archive arrived as uploaded parts and was assembled before this call.
    expect(writes).toEqual([
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
      const body = await archiveInput();
      const response = await handleReplayRequest(new Request(
        "https://example.test/api/v1/staging-archive-acceptance",
        { method: "POST", body: JSON.stringify(body) },
      ), ENV, {
        authenticate: () => Promise.resolve(),
        receiptStore: () => acceptedArchiveReceipts(body),
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
      const body = await archiveInput();
      const response = await handleReplayRequest(new Request(
        "https://example.test/api/v1/staging-archive-acceptance",
        { method: "POST", body: JSON.stringify(body) },
      ), ENV, {
        authenticate: () => Promise.resolve(),
        receiptStore: () => acceptedArchiveReceipts(body),
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

});

describe("chunked archive upload routes", () => {
  const NONCE = "1".repeat(64);
  const UPLOAD_ENABLED = { ...REVIEWED_ENV, REPLAY_ENABLED: "true" };

  /** A Sandbox that actually drains the stream it is handed, as the real one does. */
  function streamingSandbox() {
    const files = new Map<string, Uint8Array>();
    const commands: string[] = [];
    let assembleStdout: string | null = null;
    return {
      files,
      commands,
      setAssembleStdout(value: string) { assembleStdout = value; },
      client: {
        writeFile: async (path: string, contents: string | ReadableStream<Uint8Array>) => {
          if (typeof contents === "string") {
            files.set(path, new TextEncoder().encode(contents));
          } else {
            const chunks: Uint8Array[] = [];
            const reader = contents.getReader();
            let chunk = await reader.read();
            while (!chunk.done) {
              chunks.push(chunk.value);
              chunk = await reader.read();
            }
            const total = chunks.reduce((sum, part) => sum + part.byteLength, 0);
            const joined = new Uint8Array(total);
            let offset = 0;
            for (const part of chunks) {
              joined.set(part, offset);
              offset += part.byteLength;
            }
            files.set(path, joined);
          }
          return { success: true, path, timestamp: "fixture" };
        },
        exec: (command: string) => {
          commands.push(command);
          return Promise.resolve({
            success: true,
            exitCode: 0,
            stdout: assembleStdout ?? "",
            stderr: "",
            command,
            duration: 1,
            timestamp: "fixture",
          });
        },
        destroy: () => Promise.resolve(),
      },
    };
  }

  async function uploadPart(
    sandbox: ReturnType<typeof streamingSandbox>,
    receipts: ReturnType<typeof terminalReceiptStore>,
    payload: Uint8Array,
    overrides: Record<string, string> = {},
  ): Promise<Response> {
    const headers: Record<string, string> = {
      "content-type": "application/octet-stream",
      "x-lean-eval-upload-kind": "authoritative-archive",
      "x-lean-eval-runner-nonce": NONCE,
      "x-lean-eval-archive-sha256": await hexDigest(payload),
      "x-lean-eval-archive-bytes": String(payload.byteLength),
      "x-lean-eval-part-count": "1",
      "x-lean-eval-part-index": "0",
      "x-lean-eval-part-sha256": await hexDigest(payload),
      "x-lean-eval-part-bytes": String(payload.byteLength),
      ...overrides,
    };
    return handleReplayRequest(
      new Request("https://example.test/api/v1/replay/archive-part", {
        method: "POST",
        headers,
        body: payload,
      }),
      UPLOAD_ENABLED,
      {
        authenticate: () => Promise.resolve(),
        sandbox: () => sandbox.client,
        receiptStore: () => receipts,
      },
    );
  }

  it("streams a part into the sandbox without buffering the whole archive", async () => {
    const sandbox = streamingSandbox();
    const receipts = terminalReceiptStore(undefined, null);
    const payload = crypto.getRandomValues(new Uint8Array(64 * 1024));
    const response = await uploadPart(sandbox, receipts, payload);
    expect(response.status).toBe(202);
    const [path] = [...sandbox.files.keys()];
    const [written] = [...sandbox.files.values()];
    // A fresh unique name per request: a retry must never rewrite committed bytes.
    expect(path).toMatch(/^\/workspace\/archive-part-[0-9a-f-]{36}$/);
    expect(written).toEqual(payload);
  });

  it("refuses a part whose bytes do not match its declared digest", async () => {
    const sandbox = streamingSandbox();
    const receipts = terminalReceiptStore(undefined, null);
    const payload = new Uint8Array([1, 2, 3, 4]);
    const response = await uploadPart(sandbox, receipts, payload, {
      "x-lean-eval-part-sha256": "9".repeat(64),
    });
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: "invalid_request" });
  });

  it("refuses a body longer than the part it declared", async () => {
    const sandbox = streamingSandbox();
    const receipts = terminalReceiptStore(undefined, null);
    const payload = crypto.getRandomValues(new Uint8Array(4096));
    // `content-length` is client-controlled, so the count taken while streaming
    // is the only real bound.
    const response = await uploadPart(sandbox, receipts, payload, {
      "x-lean-eval-archive-bytes": "16",
      "x-lean-eval-part-bytes": "16",
      "x-lean-eval-part-sha256": await hexDigest(payload.slice(0, 16)),
      "x-lean-eval-archive-sha256": await hexDigest(payload.slice(0, 16)),
    });
    expect(response.status).toBe(400);
  });

  it("refuses a second upload binding the same nonce to a different archive", async () => {
    const sandbox = streamingSandbox();
    const receipts = terminalReceiptStore(undefined, null);
    const first = crypto.getRandomValues(new Uint8Array(1024));
    expect((await uploadPart(sandbox, receipts, first)).status).toBe(202);
    const second = crypto.getRandomValues(new Uint8Array(2048));
    const response = await uploadPart(sandbox, receipts, second);
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: "invalid_request" });
  });

  it("assembles at finalize and records readiness only on a digest match", async () => {
    const sandbox = streamingSandbox();
    const receipts = terminalReceiptStore(undefined, null);
    const payload = crypto.getRandomValues(new Uint8Array(1024));
    const digest = await hexDigest(payload);
    expect((await uploadPart(sandbox, receipts, payload)).status).toBe(202);
    sandbox.setAssembleStdout(JSON.stringify({
      schema_version: 1,
      assembled_path: "/workspace/archive.tar.gz.age",
      archive_bytes: payload.byteLength,
      archive_sha256: digest,
    }));
    const finalize = await handleReplayRequest(
      new Request("https://example.test/api/v1/replay/archive-finalize", {
        method: "POST",
        body: JSON.stringify({
          schema_version: 1,
          upload_kind: "authoritative-archive",
          runner_nonce: NONCE,
          archive_sha256: digest,
          archive_bytes: payload.byteLength,
          part_count: 1,
          parts: [{ index: 0, sha256: digest, bytes: payload.byteLength }],
        }),
      }),
      UPLOAD_ENABLED,
      {
        authenticate: () => Promise.resolve(),
        sandbox: () => sandbox.client,
        receiptStore: () => receipts,
      },
    );
    expect(finalize.status).toBe(200);
    expect(await finalize.json()).toMatchObject({ status: "assembled" });
    // Assembly is a fixed baked command driven by a manifest the Worker writes.
    expect(sandbox.commands).toEqual(["/opt/lean-eval/replay-assemble-archive"]);
    const manifest = sandbox.files.get("/workspace/archive-assembly.json");
    expect(JSON.parse(new TextDecoder().decode(manifest))).toMatchObject({
      output_path: "/workspace/archive.tar.gz.age",
      archive_sha256: digest,
    });
    expect(await receipts.readArchiveUpload()).toMatchObject({
      assembled_path: "/workspace/archive.tar.gz.age",
    });
  });

  it("refuses to finalize an upload whose parts never arrived", async () => {
    const sandbox = streamingSandbox();
    const receipts = terminalReceiptStore(undefined, null);
    const response = await handleReplayRequest(
      new Request("https://example.test/api/v1/replay/archive-finalize", {
        method: "POST",
        body: JSON.stringify({
          schema_version: 1,
          upload_kind: "authoritative-archive",
          runner_nonce: NONCE,
          archive_sha256: "4".repeat(64),
          archive_bytes: 1024,
          part_count: 1,
          parts: [{ index: 0, sha256: "5".repeat(64), bytes: 1024 }],
        }),
      }),
      UPLOAD_ENABLED,
      {
        authenticate: () => Promise.resolve(),
        sandbox: () => sandbox.client,
        receiptStore: () => receipts,
      },
    );
    expect(response.status).toBe(400);
    expect(sandbox.commands).toEqual([]);
  });

  it("refuses a start whose archive was never assembled", async () => {
    const body = await authoritativeInput();
    const sandbox = streamingSandbox();
    const response = await handleReplayRequest(
      new Request("https://example.test/api/v1/replay", {
        method: "POST",
        body: JSON.stringify(body),
      }),
      UPLOAD_ENABLED,
      {
        authenticate: () => Promise.resolve(),
        sandbox: () => sandbox.client,
        receiptStore: () => terminalReceiptStore(undefined, null),
      },
    );
    // Recoverable by re-uploading under a fresh nonce, which a start is not.
    expect(response.status).toBe(400);
    expect(sandbox.commands).toEqual([]);
  });

  it("keeps the upload routes behind the replay flag", async () => {
    const sandbox = streamingSandbox();
    let authenticated = false;
    for (const path of ["archive-part", "archive-finalize"]) {
      const response = await handleReplayRequest(
        new Request(`https://example.test/api/v1/replay/${path}`, {
          method: "POST",
          body: new Uint8Array(4),
        }),
        REVIEWED_ENV,
        {
          authenticate: () => {
            authenticated = true;
            return Promise.resolve();
          },
          sandbox: () => sandbox.client,
          receiptStore: () => terminalReceiptStore(),
        },
      );
      expect(response.status).toBe(503);
      expect(await response.json()).toEqual({ error: "replay_disabled" });
    }
    expect(authenticated).toBe(false);
  });
});
