import type { Sandbox } from "@cloudflare/sandbox";

import {
  AuthoritativeReplayContractError,
  readAuthoritativeReplayRequest,
  readAuthoritativeReplayStatusRequest,
  type AuthoritativeReplayInput,
  type AuthoritativeReplayStatusRequest,
  type ReplayVerdict,
  validateReplayVerdict,
} from "./authoritative-replay-contract";
import {
  ArchiveUploadContractError,
  MAX_PART_BYTES,
  readArchiveUploadFinalizeRequest,
  readArchiveUploadPartHeader,
  sameArchiveUploadIdentity,
  type ArchiveUploadFinalizeRequest,
  type ArchiveUploadIdentity,
  type ArchiveUploadKind,
  type ArchiveUploadPartHeader,
} from "./archive-upload-contract";
import { ReplayAuthError, type ReplayAuthEnvironment, verifyGithubOidc } from "./replay-auth";
import {
  ReplayArchiveContractError,
  readArchiveAcceptanceRequest,
  validateArchiveEvidence,
} from "./replay-archive-contract";
import {
  ReplayContractError,
  readAcceptanceRequest,
  validateSandboxEvidence,
} from "./replay-contract";
import type { ReplayTerminalReceipt } from "./replay-terminal-receipt";

export type ReplayRuntimeEnv = ReplayAuthEnvironment & {
  REPLAY_SANDBOX: DurableObjectNamespace<Sandbox>;
  REPLAY_TERMINAL_RECEIPT: DurableObjectNamespace<ReplayTerminalReceipt>;
  REPLAY_ENABLED: string;
  STAGING_ACCEPTANCE_ENABLED: string;
  STAGING_MEMORY_LIMIT_BYTES: string;
  PRODUCTION_MEMORY_GATE_BYTES: string;
  REVIEWED_EXECUTION_PROFILE_DIGEST: string;
  REVIEWED_MEASUREMENT_CONFIG_DIGEST: string;
  REVIEWED_VM_IMAGE_DIGEST: string;
};

type SandboxClient = Pick<Sandbox, "writeFile" | "exec" | "destroy"> &
  Partial<Pick<Sandbox, "startProcess" | "getProcess">>;

type TerminalReceiptStore = Pick<
  ReplayTerminalReceipt,
  | "claimBinding"
  | "readBinding"
  | "readReceipt"
  | "prepareReceipt"
  | "confirmReceipt"
  | "readArchiveUpload"
  | "claimArchiveUpload"
  | "commitArchiveUploadPart"
  | "finalizeArchiveUpload"
>;

type ExecutorFailureReason =
  | "input_transfer_failed"
  | "command_rpc_failed"
  | "command_failed"
  | "command_output_invalid"
  | "sandbox_destroy_failed"
  | "unexpected_failure";

class ReplayExecutorError extends Error {
  constructor(
    readonly reason: ExecutorFailureReason,
    readonly detail?: string,
  ) {
    super(reason);
  }
}

class ProcessStartConflictError extends ReplayExecutorError {
  constructor() {
    super("command_rpc_failed");
  }
}

type Dependencies = {
  authenticate(request: Request, env: ReplayAuthEnvironment): Promise<void>;
  sandbox(
    env: ReplayRuntimeEnv,
    runnerNonce: string,
  ): SandboxClient | Promise<SandboxClient>;
  receiptStore?(
    env: ReplayRuntimeEnv,
    runnerNonce: string,
  ): TerminalReceiptStore;
};

const DEFAULT_DEPENDENCIES: Dependencies = {
  authenticate: verifyGithubOidc,
  sandbox(): SandboxClient {
    throw new Error("sandbox dependency was not configured");
  },
};

function terminalReceiptStore(
  dependencies: Dependencies,
  env: ReplayRuntimeEnv,
  runnerNonce: string,
): TerminalReceiptStore {
  if (dependencies.receiptStore === undefined) {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  return dependencies.receiptStore(env, runnerNonce);
}

function stagingAcceptanceEnabled(env: ReplayRuntimeEnv): boolean {
  return env.DEPLOYMENT_ENVIRONMENT === "staging" && env.STAGING_ACCEPTANCE_ENABLED === "true";
}

function json(value: unknown, status = 200): Response {
  return Response.json(value, { status, headers: { "cache-control": "no-store" } });
}

const ARCHIVE_COMMAND_FAILURES = new Map([
  ["expectation is invalid", "expectation_invalid"],
  ["expectation fields are invalid", "expectation_fields_invalid"],
  ["expectation schema is invalid", "expectation_schema_invalid"],
  ["encoded input is invalid", "encoded_input_invalid"],
  ["decoded input exceeds size limit", "decoded_input_too_large"],
  ["assembled archive is unavailable", "assembled_archive_unavailable"],
  ["assembled archive exceeds its size limit", "assembled_archive_too_large"],
  ["ciphertext digest mismatch", "ciphertext_digest_mismatch"],
  ["archive decryption failed", "archive_decryption_failed"],
  ["plaintext size mismatch", "plaintext_size_mismatch"],
  ["plaintext digest mismatch", "plaintext_digest_mismatch"],
  ["decrypted archive is invalid", "archive_invalid"],
  ["decrypted archive member count is invalid", "archive_member_count_invalid"],
  ["decrypted archive contains an unsafe member", "archive_member_unsafe"],
  ["decrypted archive expands beyond its limit", "archive_expansion_too_large"],
  ["network isolation failed", "network_isolation_failed"],
]);

const AUTHORITATIVE_COMMAND_PREFIX = "replay-authoritative: ";
const AUTHORITATIVE_PROCESS_ID = "lean-eval-authoritative";
const AUTHORITATIVE_COMMAND = "/opt/lean-eval/replay-authoritative";
// Assembly is a fixed baked command with no arguments; it reads the manifest the
// Worker writes. Running it at finalize, before the one-use key unwrap, means a
// missing part or a Sandbox that idled out is discovered while the capability is
// still unspent and the upload can simply be retried.
const ARCHIVE_ASSEMBLE_COMMAND = "/opt/lean-eval/replay-assemble-archive";
const ARCHIVE_ASSEMBLE_MANIFEST = "/workspace/archive-assembly.json";
const ARCHIVE_ASSEMBLE_TIMEOUT_MS = 300_000;
// Parts sit directly in /workspace under a fixed prefix rather than in a baked
// subdirectory, which a runtime mount over /workspace could shadow.
const ARCHIVE_PART_PREFIX = "/workspace/archive-part-";
const ARCHIVE_ASSEMBLE_FAILURES = new Map([
  ["archive assembly manifest is invalid", "assembly_manifest_invalid"],
  ["archive assembly part is missing", "assembly_part_missing"],
  ["archive assembly part size mismatch", "assembly_part_size_mismatch"],
  ["archive assembly size mismatch", "assembly_size_mismatch"],
  ["archive assembly digest mismatch", "assembly_digest_mismatch"],
]);
const ARCHIVE_ASSEMBLE_PREFIX = "replay-assemble-archive: ";

/** Where each upload kind assembles, and which process later consumes it. */
const ARCHIVE_UPLOAD_TARGETS: Record<ArchiveUploadKind, string> = {
  "authoritative-archive": "/workspace/archive.tar.gz.age",
  "staging-archive-acceptance": "/workspace/archive.tar.gz.age",
};

const AUTHORITATIVE_TIMEOUT_MS = 20_100_000;
const AUTHORITATIVE_CLEANUP_AFTER_MS = 7 * 60 * 60 * 1000;
const AUTHORITATIVE_TERMINAL_RECEIPT_RETENTION_MS = 24 * 60 * 60 * 1000;
const AUTHORITATIVE_COMMAND_FAILURES = new Map([
  ["request does not match the baked profile lock", "profile_lock_mismatch"],
  ["baked benchmark identity is unavailable", "benchmark_identity_unavailable"],
  ["baked benchmark identity mismatch", "benchmark_identity_mismatch"],
  ["runtime does not match the execution profile", "runtime_profile_mismatch"],
  [
    "measurement configuration does not match the executor limits",
    "measurement_limits_mismatch",
  ],
  ["assembled archive is unavailable", "assembled_archive_unavailable"],
  ["assembled archive exceeds its size limit", "assembled_archive_too_large"],
  ["ciphertext digest mismatch", "ciphertext_digest_mismatch"],
  ["archive decryption failed", "archive_decryption_failed"],
  ["archive plaintext identity mismatch", "archive_plaintext_identity_mismatch"],
  ["archive does not contain one locked workspace", "workspace_not_found"],
  ["network isolation failed", "network_isolation_failed"],
  ["baked evaluator is unavailable", "evaluator_unavailable"],
  ["locked evaluator did not terminate", "evaluator_did_not_terminate"],
  ["evaluator failed before measurement", "evaluator_preflight_failed"],
  ["measurement evidence is unavailable", "measurement_evidence_unavailable"],
  ["evaluator results is unavailable", "evaluator_results_unavailable"],
]);

type AuthoritativeFailureBody = {
  error: "executor_failed";
  reason: ExecutorFailureReason;
  detail?: string;
};

type AuthoritativeTerminalReceipt = {
  schema_version: 1;
  binding: AuthoritativeReplayStatusRequest;
  http_status: 200 | 500;
  body:
    | { schema_version: 1; verdict: ReplayVerdict; destruction: "confirmed" }
    | AuthoritativeFailureBody;
  destruction_state: "pending" | "confirmed";
  stored_at_epoch_ms: number;
  retained_until_epoch_ms: number;
};

type AuthoritativeActiveBinding = AuthoritativeReplayStatusRequest & {
  cleanup_after_epoch_ms: number;
  retained_until_epoch_ms: number;
};

function authoritativeCommandFailureDetail(stderr: string): string {
  const output = stderr.trim();
  if (output.includes("\n") || !output.startsWith(AUTHORITATIVE_COMMAND_PREFIX)) {
    return "unclassified_authoritative_failure";
  }
  const message = output.slice(AUTHORITATIVE_COMMAND_PREFIX.length);
  const exact = AUTHORITATIVE_COMMAND_FAILURES.get(message);
  if (exact !== undefined) return exact;
  if (
    message.startsWith("measurement ")
    || message.startsWith("measured counter ")
    || message.startsWith("unavailable counter ")
    || message.startsWith("build measurement ")
    || message.startsWith("checker measurement ")
  ) {
    return "measurement_evidence_invalid";
  }
  if (
    message.startsWith("evaluator results ")
    || message.startsWith("accepted result ")
    || message.startsWith("rejected result ")
    || message.startsWith("failed result ")
    || message === "reported execution outcome is invalid"
  ) {
    return "evaluator_results_invalid";
  }
  if (
    message.startsWith("verdict ")
    || message.startsWith("statistics.")
    || message.startsWith("completed execution ")
    || message.startsWith("failed execution ")
    || message.startsWith("reported execution ")
    || message.startsWith("crash or timeout ")
    || message.startsWith("execution_outcome ")
    || message === "required retired-instruction counter was unavailable"
  ) {
    return "verdict_invalid";
  }
  if (
    message.startsWith("archive ")
    || message.startsWith("decrypted archive ")
    || message.startsWith("encoded replay input ")
    || message.startsWith("decoded replay input ")
    || message.startsWith("submission statistics ")
  ) {
    return "archive_input_invalid";
  }
  if (
    message.startsWith("execution request ")
    || message.startsWith("request ")
    || message.startsWith("profile lock ")
    || message.startsWith("archive expectation ")
    || message === "value is not canonical JSON"
  ) {
    return "execution_request_invalid";
  }
  return "unclassified_authoritative_failure";
}

function safeCommandFailureDetail(command: string, stderr: string): string | undefined {
  if (command === AUTHORITATIVE_COMMAND) {
    return authoritativeCommandFailureDetail(stderr);
  }
  if (command === "/opt/lean-eval/replay-archive-acceptance") {
    return ARCHIVE_COMMAND_FAILURES.get(stderr.trim()) ?? "unclassified_archive_failure";
  }
  if (command === ARCHIVE_ASSEMBLE_COMMAND) {
    const output = stderr.trim();
    if (output.includes("\n") || !output.startsWith(ARCHIVE_ASSEMBLE_PREFIX)) {
      return "unclassified_assembly_failure";
    }
    return ARCHIVE_ASSEMBLE_FAILURES.get(output.slice(ARCHIVE_ASSEMBLE_PREFIX.length))
      ?? "unclassified_assembly_failure";
  }
  return undefined;
}

function processAlreadyExists(error: unknown): boolean {
  return objectValue(error)?.code === "PROCESS_ALREADY_EXISTS";
}

async function startBackgroundProcess(
  sandbox: SandboxClient,
  processId: string,
  command: string,
  prepare: () => Promise<void>,
): Promise<void> {
  if (sandbox.getProcess === undefined || sandbox.startProcess === undefined) {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  let existing: Awaited<ReturnType<NonNullable<SandboxClient["getProcess"]>>>;
  try {
    existing = await sandbox.getProcess(processId);
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  if (existing !== null) return;
  await prepare();
  try {
    await sandbox.startProcess(command, {
      timeout: AUTHORITATIVE_TIMEOUT_MS,
      processId,
      autoCleanup: false,
    });
  } catch (error) {
    if (!processAlreadyExists(error)) {
      throw new ReplayExecutorError("command_rpc_failed");
    }
    // The exact duplicate may be a concurrently started winner; its sandbox must survive ambiguity.
    try {
      existing = await sandbox.getProcess(processId);
    } catch {
      throw new ProcessStartConflictError();
    }
    if (existing === null) {
      throw new ProcessStartConflictError();
    }
  }
}

async function startAuthoritativeProcess(
  sandbox: SandboxClient,
  prepare: () => Promise<void>,
): Promise<void> {
  await startBackgroundProcess(
    sandbox,
    AUTHORITATIVE_PROCESS_ID,
    AUTHORITATIVE_COMMAND,
    prepare,
  );
}

function exactObjectFields(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value).sort();
  const fields = [...expected].sort();
  return actual.length === fields.length
    && actual.every((field, index) => field === fields[index]);
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function statusBinding(input: AuthoritativeReplayInput): AuthoritativeReplayStatusRequest {
  return {
    schema_version: 1,
    runner_nonce: input.runner_nonce,
    replay_task_id: input.request.replay_task_id as string,
    attempt: input.request.attempt as number,
    execution_profile_digest: input.request.execution_profile_digest as string,
    measurement_config_digest: input.request.measurement_config_digest as string,
    vm_image_digest: (input.request.execution_profile as Record<string, unknown>)
      .vm_image_digest as string,
  };
}

function sameStatusBinding(
  value: unknown,
  request: AuthoritativeReplayStatusRequest,
): value is AuthoritativeReplayStatusRequest {
  const binding = objectValue(value);
  return binding !== null
    && exactObjectFields(binding, [
      "schema_version",
      "runner_nonce",
      "replay_task_id",
      "attempt",
      "execution_profile_digest",
      "measurement_config_digest",
      "vm_image_digest",
    ])
    && binding.schema_version === request.schema_version
    && binding.runner_nonce === request.runner_nonce
    && binding.replay_task_id === request.replay_task_id
    && binding.attempt === request.attempt
    && binding.execution_profile_digest === request.execution_profile_digest
    && binding.measurement_config_digest === request.measurement_config_digest
    && binding.vm_image_digest === request.vm_image_digest;
}

function activeBinding(
  request: AuthoritativeReplayStatusRequest,
  now = Date.now(),
): AuthoritativeActiveBinding {
  return {
    ...request,
    cleanup_after_epoch_ms: now + AUTHORITATIVE_CLEANUP_AFTER_MS,
    retained_until_epoch_ms: now + AUTHORITATIVE_TERMINAL_RECEIPT_RETENTION_MS,
  };
}

function sameActiveBinding(
  value: unknown,
  request: AuthoritativeReplayStatusRequest,
): value is AuthoritativeActiveBinding {
  const binding = objectValue(value);
  if (
    binding === null
    || !exactObjectFields(binding, [
      "schema_version",
      "runner_nonce",
      "replay_task_id",
      "attempt",
      "execution_profile_digest",
      "measurement_config_digest",
      "vm_image_digest",
      "cleanup_after_epoch_ms",
      "retained_until_epoch_ms",
    ])
    || binding.schema_version !== 1
    || typeof binding.runner_nonce !== "string"
    || typeof binding.replay_task_id !== "string"
    || !Number.isSafeInteger(binding.attempt)
    || typeof binding.execution_profile_digest !== "string"
    || typeof binding.measurement_config_digest !== "string"
    || typeof binding.vm_image_digest !== "string"
    || !Number.isSafeInteger(binding.cleanup_after_epoch_ms)
    || !Number.isSafeInteger(binding.retained_until_epoch_ms)
    || (binding.retained_until_epoch_ms as number)
      - (binding.cleanup_after_epoch_ms as number)
      !== AUTHORITATIVE_TERMINAL_RECEIPT_RETENTION_MS - AUTHORITATIVE_CLEANUP_AFTER_MS
  ) {
    return false;
  }
  return sameStatusBinding({
    schema_version: binding.schema_version,
    runner_nonce: binding.runner_nonce,
    replay_task_id: binding.replay_task_id,
    attempt: binding.attempt,
    execution_profile_digest: binding.execution_profile_digest,
    measurement_config_digest: binding.measurement_config_digest,
    vm_image_digest: binding.vm_image_digest,
  }, request);
}

function rejectBindingMismatch(value: unknown): never {
  const binding = objectValue(value);
  if (
    binding === null
    || !exactObjectFields(binding, [
      "schema_version",
      "runner_nonce",
      "replay_task_id",
      "attempt",
      "execution_profile_digest",
      "measurement_config_digest",
      "vm_image_digest",
      "cleanup_after_epoch_ms",
      "retained_until_epoch_ms",
    ])
    || binding.schema_version !== 1
    || typeof binding.runner_nonce !== "string"
    || typeof binding.replay_task_id !== "string"
    || !Number.isSafeInteger(binding.attempt)
    || typeof binding.execution_profile_digest !== "string"
    || typeof binding.measurement_config_digest !== "string"
    || typeof binding.vm_image_digest !== "string"
    || !Number.isSafeInteger(binding.cleanup_after_epoch_ms)
    || !Number.isSafeInteger(binding.retained_until_epoch_ms)
  ) {
    throw new ReplayExecutorError("command_output_invalid");
  }
  throw new AuthoritativeReplayContractError("runner nonce is already bound");
}

async function claimActiveBinding(
  store: TerminalReceiptStore,
  request: AuthoritativeReplayStatusRequest,
): Promise<AuthoritativeActiveBinding> {
  let value: unknown;
  const binding = activeBinding(request);
  try {
    value = await store.claimBinding(binding);
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  if (!sameActiveBinding(value, request)) rejectBindingMismatch(value);
  return value;
}

async function requireActiveBinding(
  store: TerminalReceiptStore,
  request: AuthoritativeReplayStatusRequest,
): Promise<void> {
  let value: unknown;
  try {
    value = await store.readBinding();
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  if (value === null) throw new ReplayExecutorError("command_rpc_failed");
  if (!sameActiveBinding(value, request)) rejectBindingMismatch(value);
}

function failureBody(error: unknown): AuthoritativeFailureBody {
  const reason = error instanceof ReplayExecutorError ? error.reason : "unexpected_failure";
  const detail = error instanceof ReplayExecutorError ? error.detail : undefined;
  return {
    error: "executor_failed",
    reason,
    ...(detail === undefined ? {} : { detail }),
  };
}

function validateTerminalReceipt(
  value: unknown,
  request: AuthoritativeReplayStatusRequest,
): AuthoritativeTerminalReceipt {
  const receipt = objectValue(value);
  if (
    receipt === null
    || !exactObjectFields(receipt, [
      "schema_version",
      "binding",
      "http_status",
      "body",
      "destruction_state",
      "stored_at_epoch_ms",
      "retained_until_epoch_ms",
    ])
    || receipt.schema_version !== 1
    || !sameStatusBinding(receipt.binding, request)
    || !["pending", "confirmed"].includes(receipt.destruction_state as string)
    || !Number.isSafeInteger(receipt.stored_at_epoch_ms)
    || !Number.isSafeInteger(receipt.retained_until_epoch_ms)
    || (receipt.retained_until_epoch_ms as number)
      !== (receipt.stored_at_epoch_ms as number) + AUTHORITATIVE_TERMINAL_RECEIPT_RETENTION_MS
  ) {
    throw new ReplayExecutorError("command_output_invalid");
  }
  const body = objectValue(receipt.body);
  if (receipt.http_status === 200 && body !== null) {
    if (
      !exactObjectFields(body, ["schema_version", "verdict", "destruction"])
      || body.schema_version !== 1
      || body.destruction !== "confirmed"
    ) {
      throw new ReplayExecutorError("command_output_invalid");
    }
    let verdict: ReplayVerdict;
    try {
      verdict = validateReplayVerdict(body.verdict, {
        request: {
          replay_task_id: request.replay_task_id,
          attempt: request.attempt,
        },
      });
    } catch {
      throw new ReplayExecutorError("command_output_invalid");
    }
    return {
      schema_version: 1,
      binding: { ...request },
      http_status: 200,
      body: { schema_version: 1, verdict, destruction: "confirmed" },
      destruction_state: receipt.destruction_state as "pending" | "confirmed",
      stored_at_epoch_ms: receipt.stored_at_epoch_ms as number,
      retained_until_epoch_ms: receipt.retained_until_epoch_ms as number,
    };
  }
  if (receipt.http_status === 500 && body !== null) {
    const hasDetail = Object.hasOwn(body, "detail");
    if (
      !exactObjectFields(body, hasDetail
        ? ["error", "reason", "detail"]
        : ["error", "reason"])
      || body.error !== "executor_failed"
      || ![
        "input_transfer_failed",
        "command_rpc_failed",
        "command_failed",
        "command_output_invalid",
        "sandbox_destroy_failed",
        "unexpected_failure",
      ].includes(body.reason as string)
      || (hasDetail
        && (typeof body.detail !== "string" || !/^[a-z0-9_]{1,64}$/.test(body.detail)))
    ) {
      throw new ReplayExecutorError("command_output_invalid");
    }
    return {
      schema_version: 1,
      binding: { ...request },
      http_status: 500,
      body: {
        error: "executor_failed",
        reason: body.reason as ExecutorFailureReason,
        ...(hasDetail ? { detail: body.detail as string } : {}),
      },
      destruction_state: receipt.destruction_state as "pending" | "confirmed",
      stored_at_epoch_ms: receipt.stored_at_epoch_ms as number,
      retained_until_epoch_ms: receipt.retained_until_epoch_ms as number,
    };
  }
  throw new ReplayExecutorError("command_output_invalid");
}

async function readTerminalReceipt(
  store: TerminalReceiptStore,
  request: AuthoritativeReplayStatusRequest,
): Promise<AuthoritativeTerminalReceipt | null> {
  let value: unknown;
  try {
    value = await store.readReceipt();
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  return value === null ? null : validateTerminalReceipt(value, request);
}

async function prepareTerminalReceipt(
  store: TerminalReceiptStore,
  receipt: AuthoritativeTerminalReceipt,
  request: AuthoritativeReplayStatusRequest,
): Promise<AuthoritativeTerminalReceipt> {
  let value: unknown;
  try {
    value = await store.prepareReceipt(receipt);
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  return validateTerminalReceipt(value, request);
}

function terminalReceiptResponse(receipt: AuthoritativeTerminalReceipt): Response {
  if (receipt.destruction_state !== "confirmed") {
    throw new ReplayExecutorError("sandbox_destroy_failed");
  }
  return json(receipt.body, receipt.http_status);
}

async function confirmTerminalReceipt(
  sandbox: SandboxClient,
  store: TerminalReceiptStore,
  receipt: AuthoritativeTerminalReceipt,
): Promise<AuthoritativeTerminalReceipt> {
  if (receipt.destruction_state === "confirmed") return receipt;
  try {
    await sandbox.destroy();
  } catch {
    throw new ReplayExecutorError("sandbox_destroy_failed");
  }
  const confirmed: AuthoritativeTerminalReceipt = {
    ...receipt,
    destruction_state: "confirmed",
  };
  let value: unknown;
  try {
    value = await store.confirmReceipt();
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  return validateTerminalReceipt(value, confirmed.binding);
}

function terminalReceipt(
  request: AuthoritativeReplayStatusRequest,
  status: string,
  logs: { stdout: string; stderr: string },
  now = Date.now(),
): AuthoritativeTerminalReceipt {
  let httpStatus: 200 | 500;
  let body: AuthoritativeTerminalReceipt["body"];
  try {
    if (status !== "completed") {
      throw new ReplayExecutorError(
        "command_failed",
        safeCommandFailureDetail(AUTHORITATIVE_COMMAND, logs.stderr),
      );
    }
    if (logs.stdout.length > 64 * 1024) {
      throw new ReplayExecutorError("command_output_invalid");
    }
    let verdict: ReplayVerdict;
    try {
      verdict = validateReplayVerdict(JSON.parse(logs.stdout) as unknown, {
        request: {
          replay_task_id: request.replay_task_id,
          attempt: request.attempt,
        },
      });
    } catch {
      throw new ReplayExecutorError("command_output_invalid");
    }
    httpStatus = 200;
    body = { schema_version: 1, verdict, destruction: "confirmed" };
  } catch (error) {
    recordExecutorFailure("authoritative_replay_status", error);
    httpStatus = 500;
    body = failureBody(error);
  }
  return {
    schema_version: 1,
    binding: { ...request },
    http_status: httpStatus,
    body,
    destruction_state: "pending",
    stored_at_epoch_ms: now,
    retained_until_epoch_ms: now + AUTHORITATIVE_TERMINAL_RECEIPT_RETENTION_MS,
  };
}

async function authoritativeProcessStatus(
  sandbox: SandboxClient,
  store: TerminalReceiptStore,
  request: AuthoritativeReplayStatusRequest,
): Promise<Response> {
  const stored = await readTerminalReceipt(store, request);
  if (stored !== null) {
    return terminalReceiptResponse(await confirmTerminalReceipt(sandbox, store, stored));
  }
  if (sandbox.getProcess === undefined) throw new ReplayExecutorError("command_rpc_failed");
  let process: Awaited<ReturnType<NonNullable<SandboxClient["getProcess"]>>>;
  try {
    process = await sandbox.getProcess(AUTHORITATIVE_PROCESS_ID);
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  if (process === null) throw new ReplayExecutorError("command_rpc_failed");
  let status: Awaited<ReturnType<typeof process.getStatus>>;
  try {
    status = await process.getStatus();
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  if (status === "starting" || status === "running") {
    return json({
      schema_version: 1,
      replay_task_id: request.replay_task_id,
      attempt: request.attempt,
      status: "running",
    }, 202);
  }
  let logs: Awaited<ReturnType<typeof process.getLogs>>;
  try {
    logs = await process.getLogs();
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  const receipt = await prepareTerminalReceipt(
    store,
    terminalReceipt(request, status, logs),
    request,
  );
  return terminalReceiptResponse(await confirmTerminalReceipt(sandbox, store, receipt));
}

async function writeSandboxFile(
  sandbox: SandboxClient,
  path: string,
  contents: string | ReadableStream<Uint8Array>,
): Promise<void> {
  let result: Awaited<ReturnType<SandboxClient["writeFile"]>>;
  try {
    result = await sandbox.writeFile(path, contents);
  } catch {
    throw new ReplayExecutorError("input_transfer_failed");
  }
  if (!result.success || result.path !== path) {
    throw new ReplayExecutorError("input_transfer_failed");
  }
}

/**
 * Stream one upload part into the Sandbox, digesting and counting as it passes.
 *
 * A pass-through transform rather than `body.tee()`: with `tee`, the digest
 * branch drains faster than the Sandbox write and the runtime buffers the whole
 * part for the slower branch, which is the memory behaviour this transport
 * exists to remove. Here nothing larger than one chunk is ever resident.
 *
 * `content-length` is optional and client-controlled, so the count taken here is
 * the only real bound on how many bytes a part may carry.
 */
async function streamPartToSandbox(
  sandbox: SandboxClient,
  path: string,
  body: ReadableStream<Uint8Array>,
  maximumBytes: number,
): Promise<{ bytes: number; sha256: string }> {
  const digestStream = new DigestStream("SHA-256");
  const writer = digestStream.getWriter();
  let bytes = 0;
  const overflow = { hit: false };
  const counted = body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    async transform(chunk, controller) {
      bytes += chunk.byteLength;
      if (bytes > maximumBytes) {
        overflow.hit = true;
        throw new Error("upload part exceeds its size limit");
      }
      await writer.write(chunk);
      controller.enqueue(chunk);
    },
    async flush() {
      await writer.close();
    },
  }));
  let result: Awaited<ReturnType<SandboxClient["writeFile"]>>;
  try {
    result = await sandbox.writeFile(path, counted);
  } catch {
    if (overflow.hit) throw new ArchiveUploadContractError("upload part exceeds its size limit");
    throw new ReplayExecutorError("input_transfer_failed");
  }
  if (!result.success || result.path !== path) {
    throw new ReplayExecutorError("input_transfer_failed");
  }
  // `WriteFileResult` carries no byte count, so the Worker cannot confirm here
  // that the container received everything it was sent. The assembly helper
  // re-measures every part against the manifest before the key unwrap, which
  // catches a truncated transfer while the capability is still unspent.
  const digest = new Uint8Array(await digestStream.digest);
  return {
    bytes,
    sha256: [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join(""),
  };
}

function uploadIdentity(value: {
  upload_kind: ArchiveUploadKind;
  runner_nonce: string;
  archive_sha256: string;
  archive_bytes: number;
  part_count: number;
}): ArchiveUploadIdentity {
  return {
    schema_version: 1,
    upload_kind: value.upload_kind,
    runner_nonce: value.runner_nonce,
    archive_sha256: value.archive_sha256,
    archive_bytes: value.archive_bytes,
    part_count: value.part_count,
  };
}

/**
 * Accept one part. Claiming happens here, on the first part, rather than at the
 * replay start: the upload creates the Sandbox, so the durable record that will
 * later destroy an abandoned one has to exist from the first byte.
 */
async function handleArchiveUploadPart(
  request: Request,
  header: ArchiveUploadPartHeader,
  store: TerminalReceiptStore,
  sandbox: SandboxClient,
): Promise<Response> {
  const body = request.body;
  if (body === null) throw new ArchiveUploadContractError("upload part requires a body");
  const identity = uploadIdentity(header);
  await store.claimArchiveUpload(identity);
  // A fresh path per request. Deterministic per-index names would let a retry or
  // a delayed duplicate rewrite bytes that an earlier part already committed,
  // including after finalize had accepted them.
  const path = `${ARCHIVE_PART_PREFIX}${crypto.randomUUID()}`;
  const written = await streamPartToSandbox(
    sandbox,
    path,
    body as ReadableStream<Uint8Array>,
    MAX_PART_BYTES,
  );
  if (written.bytes !== header.part_bytes || written.sha256 !== header.part_sha256) {
    throw new ArchiveUploadContractError("upload part does not match its declared digest");
  }
  await store.commitArchiveUploadPart(identity, {
    index: header.part_index,
    sha256: written.sha256,
    bytes: written.bytes,
    path,
  });
  return json({
    schema_version: 1,
    upload_kind: header.upload_kind,
    part_index: header.part_index,
    status: "stored",
  }, 202);
}

/**
 * Assemble and verify inside the container, before any key unwrap. Verifying
 * only in the replay image would be enough for integrity but not for readiness:
 * a missing part or an idled-out container would then surface after the one-use
 * capability had been spent.
 */
async function handleArchiveUploadFinalize(
  finalize: ArchiveUploadFinalizeRequest,
  store: TerminalReceiptStore,
  sandbox: SandboxClient,
): Promise<Response> {
  const identity = uploadIdentity(finalize);
  const stored = objectValue(await store.readArchiveUpload());
  if (stored === null) throw new ArchiveUploadContractError("archive upload was not claimed");
  const committed = Array.isArray(stored.parts) ? stored.parts : [];
  if (
    !sameArchiveUploadIdentity(
      uploadIdentity(stored as unknown as ArchiveUploadIdentity),
      identity,
    )
    || committed.length !== finalize.part_count
  ) {
    throw new ArchiveUploadContractError("archive upload does not match the finalize request");
  }
  const ordered = finalize.parts.map((expected) => {
    const match = committed
      .map((entry) => objectValue(entry))
      .find((entry): entry is Record<string, unknown> => entry !== null && entry.index === expected.index);
    if (
      match?.sha256 !== expected.sha256
      || match.bytes !== expected.bytes
      || typeof match.path !== "string"
    ) {
      throw new ArchiveUploadContractError("archive upload part does not match the finalize request");
    }
    return { index: expected.index, path: match.path, bytes: expected.bytes, sha256: expected.sha256 };
  });
  const target = ARCHIVE_UPLOAD_TARGETS[finalize.upload_kind];
  await writeSandboxFile(sandbox, ARCHIVE_ASSEMBLE_MANIFEST, JSON.stringify({
    schema_version: 1,
    output_path: target,
    archive_sha256: finalize.archive_sha256,
    archive_bytes: finalize.archive_bytes,
    parts: ordered,
  }));
  const stdout = await executeSandboxCommand(
    sandbox,
    ARCHIVE_ASSEMBLE_COMMAND,
    ARCHIVE_ASSEMBLE_TIMEOUT_MS,
    4096,
  );
  let assembled: Record<string, unknown> | null;
  try {
    assembled = objectValue(JSON.parse(stdout) as unknown);
  } catch {
    throw new ReplayExecutorError("command_output_invalid");
  }
  if (assembled === null) throw new ReplayExecutorError("command_output_invalid");
  if (
    assembled.schema_version !== 1
    || assembled.assembled_path !== target
    || assembled.archive_bytes !== finalize.archive_bytes
    || assembled.archive_sha256 !== finalize.archive_sha256
  ) {
    throw new ReplayExecutorError("command_output_invalid");
  }
  await store.finalizeArchiveUpload(identity, target);
  return json({
    schema_version: 1,
    upload_kind: finalize.upload_kind,
    status: "assembled",
  }, 200);
}

/** The assembled archive a replay start may use, or null if none is ready. */
async function readyArchiveUpload(
  store: TerminalReceiptStore,
  expected: ArchiveUploadIdentity,
): Promise<string> {
  const stored = objectValue(await store.readArchiveUpload());
  if (stored === null) {
    throw new ArchiveUploadContractError("archive upload was not completed");
  }
  if (
    !sameArchiveUploadIdentity(uploadIdentity(stored as unknown as ArchiveUploadIdentity), expected)
    || typeof stored.assembled_path !== "string"
  ) {
    throw new ArchiveUploadContractError("archive upload does not match the replay request");
  }
  return stored.assembled_path;
}

async function executeSandboxCommand(
  sandbox: SandboxClient,
  command: string,
  timeout: number,
  maximumStdout: number,
): Promise<string> {
  let result: Awaited<ReturnType<SandboxClient["exec"]>>;
  try {
    result = await sandbox.exec(command, { timeout });
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  if (!result.success) {
    throw new ReplayExecutorError(
      "command_failed",
      safeCommandFailureDetail(command, result.stderr),
    );
  }
  if (result.stdout.length > maximumStdout) {
    throw new ReplayExecutorError("command_output_invalid");
  }
  return result.stdout;
}

async function withSandboxDestruction<T>(
  sandbox: SandboxClient,
  operation: () => T | Promise<T>,
): Promise<T> {
  let outcome: { ok: true; value: T } | { ok: false; error: unknown };
  try {
    outcome = { ok: true, value: await operation() };
  } catch (error) {
    outcome = { ok: false, error };
  }
  let destructionFailed = false;
  try {
    await sandbox.destroy();
  } catch {
    destructionFailed = true;
  }
  if (!outcome.ok) throw outcome.error;
  if (destructionFailed) throw new ReplayExecutorError("sandbox_destroy_failed");
  return outcome.value;
}

function recordExecutorFailure(route: string, error: unknown): void {
  const reason = error instanceof ReplayExecutorError ? error.reason : "unexpected_failure";
  const detail = error instanceof ReplayExecutorError ? error.detail : undefined;
  console.error(JSON.stringify({
    event: "lean_eval_replay_executor_failure",
    route,
    reason,
    ...(detail === undefined ? {} : { detail }),
  }));
}

function authoritativeExecutorFailure(error: unknown): Response {
  return json(failureBody(error), 500);
}

function health(env: ReplayRuntimeEnv): Response {
  return json({
    status: "ok",
    service: "lean-eval-replay-executor",
    environment: env.DEPLOYMENT_ENVIRONMENT,
    deployed_commit: env.DEPLOYED_COMMIT,
    replay_enabled: env.REPLAY_ENABLED === "true",
    staging_acceptance_enabled: env.STAGING_ACCEPTANCE_ENABLED === "true",
    staging_memory_limit_bytes: Number(env.STAGING_MEMORY_LIMIT_BYTES),
    production_memory_gate_bytes: Number(env.PRODUCTION_MEMORY_GATE_BYTES),
    reviewed_execution_profile_digest: env.REVIEWED_EXECUTION_PROFILE_DIGEST,
    reviewed_measurement_config_digest: env.REVIEWED_MEASUREMENT_CONFIG_DIGEST,
    reviewed_vm_image_digest: env.REVIEWED_VM_IMAGE_DIGEST,
  });
}

export async function handleReplayRequest(
  request: Request,
  env: ReplayRuntimeEnv,
  dependencies: Dependencies = DEFAULT_DEPENDENCIES,
): Promise<Response> {
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/healthz") return health(env);
  const syntheticAcceptance = url.pathname === "/api/v1/staging-acceptance";
  const archiveAcceptance = url.pathname === "/api/v1/staging-archive-acceptance";
  const authoritativeReplay = url.pathname === "/api/v1/replay";
  const authoritativeStatus = url.pathname === "/api/v1/replay/status";
  const authoritativePart = url.pathname === "/api/v1/replay/archive-part";
  const authoritativeFinalize = url.pathname === "/api/v1/replay/archive-finalize";
  const archivePart = url.pathname === "/api/v1/staging-archive-acceptance/archive-part";
  const archiveFinalize = url.pathname === "/api/v1/staging-archive-acceptance/archive-finalize";
  if (
    (
      !syntheticAcceptance
      && !archiveAcceptance
      && !authoritativeReplay
      && !authoritativeStatus
      && !authoritativePart
      && !authoritativeFinalize
      && !archivePart
      && !archiveFinalize
    ) ||
    request.method !== "POST"
  ) {
    return json({ error: "not_found" }, 404);
  }
  // Upload shares the replay flag: it creates the same Sandbox the start would,
  // so it must not be reachable while replay is disabled.
  if ((authoritativeReplay || authoritativePart || authoritativeFinalize) && env.REPLAY_ENABLED !== "true") {
    return json({ error: "replay_disabled" }, 503);
  }
  if (authoritativePart || authoritativeFinalize || archivePart || archiveFinalize) {
    const kind: ArchiveUploadKind = authoritativePart || authoritativeFinalize
      ? "authoritative-archive"
      : "staging-archive-acceptance";
    const route = `${kind === "authoritative-archive" ? "authoritative" : "staging_archive"}_upload`;
    if (kind === "staging-archive-acceptance" && !stagingAcceptanceEnabled(env)) {
      return json({ error: "staging_acceptance_disabled" }, 503);
    }
    try {
      await dependencies.authenticate(request, env);
      // Parse before resolving the Sandbox: the runner nonce names the Sandbox
      // and the durable record, and a body can only be read once.
      if (authoritativePart || archivePart) {
        const header = readArchiveUploadPartHeader(request);
        if (header.upload_kind !== kind) {
          throw new ArchiveUploadContractError("upload_kind does not match this endpoint");
        }
        const store = terminalReceiptStore(dependencies, env, header.runner_nonce);
        const sandbox = await dependencies.sandbox(env, header.runner_nonce);
        return await handleArchiveUploadPart(request, header, store, sandbox);
      }
      const finalize = await readArchiveUploadFinalizeRequest(request);
      if (finalize.upload_kind !== kind) {
        throw new ArchiveUploadContractError("upload_kind does not match this endpoint");
      }
      const store = terminalReceiptStore(dependencies, env, finalize.runner_nonce);
      const sandbox = await dependencies.sandbox(env, finalize.runner_nonce);
      return await handleArchiveUploadFinalize(finalize, store, sandbox);
    } catch (error) {
      if (error instanceof ReplayAuthError) return json({ error: "unauthorized" }, 401);
      if (error instanceof ArchiveUploadContractError || error instanceof SyntaxError) {
        return json({ error: "invalid_request" }, 400);
      }
      recordExecutorFailure(route, error);
      return authoritativeExecutorFailure(error);
    }
  }
  if (authoritativeStatus) {
    let sandbox: SandboxClient | undefined;
    try {
      await dependencies.authenticate(request, env);
      const input = await readAuthoritativeReplayStatusRequest(
        request,
        env.REVIEWED_EXECUTION_PROFILE_DIGEST,
        env.REVIEWED_MEASUREMENT_CONFIG_DIGEST,
        env.REVIEWED_VM_IMAGE_DIGEST,
      );
      const store = terminalReceiptStore(dependencies, env, input.runner_nonce);
      await requireActiveBinding(store, input);
      sandbox = await dependencies.sandbox(env, input.runner_nonce);
      return await authoritativeProcessStatus(sandbox, store, input);
    } catch (error) {
      if (error instanceof ReplayAuthError) return json({ error: "unauthorized" }, 401);
      if (error instanceof AuthoritativeReplayContractError || error instanceof SyntaxError) {
        return json({ error: "invalid_request" }, 400);
      }
      recordExecutorFailure("authoritative_replay_status", error);
      return authoritativeExecutorFailure(error);
    }
  }
  if (authoritativeReplay) {
    try {
      await dependencies.authenticate(request, env);
      const input = await readAuthoritativeReplayRequest(
        request,
        env.REVIEWED_EXECUTION_PROFILE_DIGEST,
        env.REVIEWED_MEASUREMENT_CONFIG_DIGEST,
        env.REVIEWED_VM_IMAGE_DIGEST,
      );
      const store = terminalReceiptStore(dependencies, env, input.runner_nonce);
      const binding = statusBinding(input);
      await claimActiveBinding(store, binding);
      const existingReceipt = await readTerminalReceipt(store, binding);
      if (existingReceipt !== null) {
        return json({
          schema_version: 1,
          replay_task_id: input.request.replay_task_id,
          attempt: input.request.attempt,
          status: "running",
        }, 202);
      }
      // The archive is already in the Sandbox, assembled and digest-checked by
      // the finalize that ran before the key unwrap. A start that cannot see it
      // must not proceed: the capability is spent by now, but a refusal here is
      // recoverable by re-uploading under a fresh nonce, and a start is not.
      await readyArchiveUpload(store, {
        schema_version: 1,
        upload_kind: "authoritative-archive",
        runner_nonce: input.runner_nonce,
        archive_sha256: input.archive_expectation.archive_ciphertext_sha256,
        archive_bytes: input.archive_ciphertext_bytes,
        part_count: input.archive_part_count,
      });
      const sandbox = await dependencies.sandbox(env, input.runner_nonce);
      try {
        await startAuthoritativeProcess(sandbox, async () => {
          await writeSandboxFile(sandbox, "/workspace/replay-request.json", JSON.stringify(input.request));
          await writeSandboxFile(
            sandbox,
            "/workspace/archive-expectation.json",
            JSON.stringify(input.archive_expectation),
          );
          if (input.key_material_type === "age-identity-v1") {
            await writeSandboxFile(sandbox, "/workspace/identity.age.b64", input.plaintext_key_material_base64);
          } else {
            await writeSandboxFile(sandbox, "/workspace/key-material.b64", input.plaintext_key_material_base64);
          }
        });
      } catch (error) {
        if (!(error instanceof ProcessStartConflictError)) {
          await sandbox.destroy();
        }
        throw error;
      }
      return json({
        schema_version: 1,
        replay_task_id: input.request.replay_task_id,
        attempt: input.request.attempt,
        status: "running",
      }, 202);
    } catch (error) {
      if (error instanceof ReplayAuthError) return json({ error: "unauthorized" }, 401);
      if (
        error instanceof AuthoritativeReplayContractError
        || error instanceof ArchiveUploadContractError
        || error instanceof SyntaxError
      ) {
        return json({ error: "invalid_request" }, 400);
      }
      recordExecutorFailure("authoritative_replay", error);
      return authoritativeExecutorFailure(error);
    }
  }
  if (env.DEPLOYMENT_ENVIRONMENT !== "staging" || env.STAGING_ACCEPTANCE_ENABLED !== "true") {
    return json({ error: "staging_acceptance_disabled" }, 503);
  }
  try {
    await dependencies.authenticate(request, env);
    if (archiveAcceptance) {
      const input = await readArchiveAcceptanceRequest(request);
      const store = terminalReceiptStore(dependencies, env, input.runner_nonce);
      // The archive was uploaded and assembled under this nonce before this
      // request; refusing here keeps a stale or absent upload from being read
      // as a decryption failure by the acceptance run.
      await readyArchiveUpload(store, {
        schema_version: 1,
        upload_kind: "staging-archive-acceptance",
        runner_nonce: input.runner_nonce,
        archive_sha256: input.archive_ciphertext_sha256,
        archive_bytes: input.archive_ciphertext_bytes,
        part_count: input.archive_part_count,
      });
      const sandbox = await dependencies.sandbox(env, input.runner_nonce);
      const evidence = await withSandboxDestruction(sandbox, async () => {
          await writeSandboxFile(sandbox, "/workspace/identity.age.b64", input.plaintext_identity_base64);
          await writeSandboxFile(
            sandbox,
            "/workspace/archive-expectation.json",
            JSON.stringify({
              schema_version: 1,
              submission_id: input.submission_id,
              archive_ciphertext_sha256: input.archive_ciphertext_sha256,
              plaintext_tar_sha256: input.plaintext_tar_sha256,
              plaintext_tar_size: input.plaintext_tar_size,
            }),
          );
          const stdout = await executeSandboxCommand(
            sandbox,
            "/opt/lean-eval/replay-archive-acceptance",
            180_000,
            4096,
          );
          try {
            return validateArchiveEvidence(JSON.parse(stdout) as unknown, input);
          } catch {
            throw new ReplayExecutorError("command_output_invalid");
          }
      });
      return json({
        schema_version: 1,
        service: "lean-eval-replay-executor",
        environment: "staging",
        request_id: input.request_id,
        runner_nonce: input.runner_nonce,
        submission_id: evidence.submission_id,
        archive_ciphertext_sha256: evidence.archive_ciphertext_sha256,
        plaintext_tar_sha256: evidence.plaintext_tar_sha256,
        plaintext_tar_size: evidence.plaintext_tar_size,
        network_policy: "disabled",
        network_probe: evidence.network_probe,
        destruction: "confirmed",
        architecture: evidence.architecture,
        kernel_release: evidence.kernel_release,
        cpu_model: evidence.cpu_model,
        staging_memory_limit_bytes: Number(env.STAGING_MEMORY_LIMIT_BYTES),
        production_memory_gate_bytes: Number(env.PRODUCTION_MEMORY_GATE_BYTES),
      });
    }
    const input = await readAcceptanceRequest(request);
    const sandbox = await dependencies.sandbox(env, input.runner_nonce);
    const evidence = await withSandboxDestruction(sandbox, async () => {
        await writeSandboxFile(sandbox, "/workspace/archive.tar.gz.age.b64", input.ciphertext_base64);
        await writeSandboxFile(sandbox, "/workspace/identity.age.b64", input.plaintext_identity_base64);
        await writeSandboxFile(
          sandbox,
          "/workspace/expectation.json",
          JSON.stringify({
            schema_version: 1,
            archive_ciphertext_sha256: input.archive_ciphertext_sha256,
            marker_sha256: input.marker_sha256,
          }),
        );
        const stdout = await executeSandboxCommand(
          sandbox,
          "/opt/lean-eval/replay-staging-acceptance",
          120_000,
          4096,
        );
        try {
          return validateSandboxEvidence(JSON.parse(stdout) as unknown, input);
        } catch {
          throw new ReplayExecutorError("command_output_invalid");
        }
    });
    return json({
      schema_version: 1,
      service: "lean-eval-replay-executor",
      environment: "staging",
      request_id: input.request_id,
      runner_nonce: input.runner_nonce,
      archive_ciphertext_sha256: evidence.archive_ciphertext_sha256,
      marker_sha256: evidence.marker_sha256,
      network_policy: "disabled",
      network_probe: evidence.network_probe,
      destruction: "confirmed",
      architecture: evidence.architecture,
      kernel_release: evidence.kernel_release,
      cpu_model: evidence.cpu_model,
      staging_memory_limit_bytes: Number(env.STAGING_MEMORY_LIMIT_BYTES),
      production_memory_gate_bytes: Number(env.PRODUCTION_MEMORY_GATE_BYTES),
    });
  } catch (error) {
    if (error instanceof ReplayAuthError) return json({ error: "unauthorized" }, 401);
    if (
      error instanceof ReplayContractError ||
      error instanceof ReplayArchiveContractError ||
      error instanceof SyntaxError
    ) {
      return json({ error: "invalid_request" }, 400);
    }
    recordExecutorFailure(archiveAcceptance ? "archive_acceptance" : "synthetic_acceptance", error);
    return json(failureBody(error), 500);
  }
}
