import type { Sandbox } from "@cloudflare/sandbox";

import {
  canonicalHistoricalPublicHandoff,
  HistoricalPublicExecutorContractError,
  MAX_REPLAY_ATTEMPTS,
  historicalPublicExecutorVerdictFromBinding,
  historicalPublicRunnerBinding,
  readHistoricalPublicExecutorRequest,
  readHistoricalPublicExecutorStatusRequest,
  type HistoricalPublicExecutorInput,
  type HistoricalPublicExecutorStatusRequest,
  type HistoricalPublicExecutorVerdict,
  type HistoricalPublicRunnerBinding,
} from "./historical-public-executor-contract";
import {
  AuthoritativeReplayContractError,
  readAuthoritativeReplayRequest,
  readAuthoritativeReplayStatusRequest,
  type AuthoritativeReplayInput,
  type AuthoritativeReplayStatusRequest,
  type ReplayVerdict,
  validateReplayVerdict,
} from "./authoritative-replay-contract";
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
  HISTORICAL_PUBLIC_REPLAY_ENABLED: string;
  STAGING_ACCEPTANCE_ENABLED: string;
  STAGING_MEMORY_LIMIT_BYTES: string;
  PRODUCTION_MEMORY_GATE_BYTES: string;
  REVIEWED_EXECUTION_PROFILE_DIGEST: string;
  REVIEWED_MEASUREMENT_CONFIG_DIGEST: string;
  REVIEWED_VM_IMAGE_DIGEST: string;
  EXPECTED_REPLAY_TASK_ID?: string;
  EXPECTED_REPLAY_ATTEMPT?: string;
  EXECUTOR_OWNERSHIP_TAG?: string;
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
>;

type HistoricalCleanupStore = Pick<
  ReplayTerminalReceipt,
  "destroyBoundSandbox" | "reserveCleanupIdentity"
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
  sandbox(env: ReplayRuntimeEnv, runnerNonce: string): SandboxClient;
  receiptStore?(
    env: ReplayRuntimeEnv,
    runnerNonce: string,
    historicalIdentity?: HistoricalPublicExecutorStatusRequest,
  ): TerminalReceiptStore;
  recoveryStore?(
    env: ReplayRuntimeEnv,
    replayTaskId: string,
    attempt: number,
  ): HistoricalCleanupStore;
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
  historicalIdentity?: HistoricalPublicExecutorStatusRequest,
): TerminalReceiptStore {
  if (dependencies.receiptStore === undefined) {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  return dependencies.receiptStore(env, runnerNonce, historicalIdentity);
}

function json(value: unknown, status = 200): Response {
  return Response.json(value, { status, headers: { "cache-control": "no-store" } });
}

function requireHistoricalPrivateBinding(
  env: ReplayRuntimeEnv,
  value: { replay_task_id: string; attempt: number },
): void {
  if (env.DEPLOYMENT_ENVIRONMENT !== "historical-private-replay") return;
  if (
    env.EXPECTED_REPLAY_TASK_ID === undefined
    || !REPLAY_TASK_ID.test(env.EXPECTED_REPLAY_TASK_ID)
    || value.replay_task_id !== env.EXPECTED_REPLAY_TASK_ID
    || env.EXPECTED_REPLAY_ATTEMPT !== String(value.attempt)
  ) {
    throw new AuthoritativeReplayContractError(
      "execution is not the exact historical private replay binding",
    );
  }
}

const ARCHIVE_COMMAND_FAILURES = new Map([
  ["expectation is invalid", "expectation_invalid"],
  ["expectation fields are invalid", "expectation_fields_invalid"],
  ["expectation schema is invalid", "expectation_schema_invalid"],
  ["encoded input is invalid", "encoded_input_invalid"],
  ["decoded input exceeds size limit", "decoded_input_too_large"],
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
const SHA256_DIGEST = /^[0-9a-f]{64}$/;
const OCI_SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/;
const REPLAY_TASK_ID = /^rt1_[0-9a-f]{64}$/;
const HISTORICAL_REQUEST_ID = /^prr_[0-9a-f]{64}$/;
const RESULT_ID = /^r2_[0-9a-f]{64}$/;
const AUTHORITATIVE_PROCESS_ID = "lean-eval-authoritative";
const AUTHORITATIVE_COMMAND = "/opt/lean-eval/replay-authoritative";
const HISTORICAL_PUBLIC_PROCESS_ID = "lean-eval-historical-public";
const HISTORICAL_PUBLIC_COMMAND =
  "base64 --decode /workspace/historical-public-source.tar.gz.b64 "
  + "> /workspace/historical-public-source.tar.gz "
  + "&& rm /workspace/historical-public-source.tar.gz.b64 "
  + "&& /opt/lean-eval/historical-public-runner";
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

type HistoricalPublicProcessBinding = HistoricalPublicExecutorStatusRequest
  & HistoricalPublicRunnerBinding;

type HistoricalPublicActiveBinding = HistoricalPublicProcessBinding & {
  cleanup_after_epoch_ms: number;
  retained_until_epoch_ms: number;
};

type HistoricalPublicTerminalReceipt = {
  schema_version: 1;
  binding: HistoricalPublicProcessBinding;
  http_status: 200 | 500;
  body: HistoricalPublicExecutorVerdict | AuthoritativeFailureBody;
  destruction_state: "pending" | "confirmed";
  stored_at_epoch_ms: number;
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

async function startHistoricalPublicProcess(
  sandbox: SandboxClient,
  prepare: () => Promise<void>,
): Promise<void> {
  await startBackgroundProcess(
    sandbox,
    HISTORICAL_PUBLIC_PROCESS_ID,
    HISTORICAL_PUBLIC_COMMAND,
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

function historicalStatusBinding(
  input: HistoricalPublicExecutorInput,
): HistoricalPublicExecutorStatusRequest {
  return {
    schema_version: 1,
    runner_nonce: input.runner_nonce,
    replay_task_id: input.replay_task_id,
    attempt: input.attempt,
    handoff_sha256: input.handoff_sha256,
    source_archive_sha256: input.source_archive_sha256,
    execution_profile_digest: input.execution_profile_digest,
    measurement_config_digest: input.measurement_config_digest,
    vm_image_digest: input.vm_image_digest,
  };
}

function historicalProcessBinding(
  input: HistoricalPublicExecutorInput,
): HistoricalPublicProcessBinding {
  return {
    ...historicalStatusBinding(input),
    ...historicalPublicRunnerBinding(input),
  };
}

function historicalProcessBindingValue(
  value: unknown,
): HistoricalPublicProcessBinding | null {
  const binding = objectValue(value);
  if (
    binding === null
    || !exactObjectFields(binding, [
      "schema_version",
      "runner_nonce",
      "replay_task_id",
      "attempt",
      "handoff_sha256",
      "source_archive_sha256",
      "execution_profile_digest",
      "measurement_config_digest",
      "vm_image_digest",
      "request_id",
      "result_id",
    ])
    || binding.schema_version !== 1
    || typeof binding.runner_nonce !== "string"
    || !SHA256_DIGEST.test(binding.runner_nonce)
    || typeof binding.replay_task_id !== "string"
    || !REPLAY_TASK_ID.test(binding.replay_task_id)
    || !Number.isSafeInteger(binding.attempt)
    || (binding.attempt as number) < 1
    || typeof binding.handoff_sha256 !== "string"
    || !SHA256_DIGEST.test(binding.handoff_sha256)
    || typeof binding.source_archive_sha256 !== "string"
    || !SHA256_DIGEST.test(binding.source_archive_sha256)
    || typeof binding.execution_profile_digest !== "string"
    || !SHA256_DIGEST.test(binding.execution_profile_digest)
    || typeof binding.measurement_config_digest !== "string"
    || !SHA256_DIGEST.test(binding.measurement_config_digest)
    || typeof binding.vm_image_digest !== "string"
    || !OCI_SHA256_DIGEST.test(binding.vm_image_digest)
    || typeof binding.request_id !== "string"
    || !HISTORICAL_REQUEST_ID.test(binding.request_id)
    || typeof binding.result_id !== "string"
    || !RESULT_ID.test(binding.result_id)
  ) {
    return null;
  }
  return binding as HistoricalPublicProcessBinding;
}

function sameHistoricalStatusBinding(
  value: unknown,
  request: HistoricalPublicExecutorStatusRequest,
): value is HistoricalPublicExecutorStatusRequest {
  const binding = objectValue(value);
  return binding !== null
    && exactObjectFields(binding, [
      "schema_version",
      "runner_nonce",
      "replay_task_id",
      "attempt",
      "handoff_sha256",
      "source_archive_sha256",
      "execution_profile_digest",
      "measurement_config_digest",
      "vm_image_digest",
    ])
    && binding.schema_version === request.schema_version
    && binding.runner_nonce === request.runner_nonce
    && binding.replay_task_id === request.replay_task_id
    && binding.attempt === request.attempt
    && binding.handoff_sha256 === request.handoff_sha256
    && binding.source_archive_sha256 === request.source_archive_sha256
    && binding.execution_profile_digest === request.execution_profile_digest
    && binding.measurement_config_digest === request.measurement_config_digest
    && binding.vm_image_digest === request.vm_image_digest;
}

function sameHistoricalProcessBinding(
  value: unknown,
  request: HistoricalPublicProcessBinding,
): value is HistoricalPublicProcessBinding {
  const binding = historicalProcessBindingValue(value);
  return binding !== null
    && sameHistoricalStatusBinding(historicalStatusBindingFromProcess(binding), request)
    && binding.request_id === request.request_id
    && binding.result_id === request.result_id;
}

function historicalStatusBindingFromProcess(
  binding: HistoricalPublicProcessBinding,
): HistoricalPublicExecutorStatusRequest {
  return {
    schema_version: 1,
    runner_nonce: binding.runner_nonce,
    replay_task_id: binding.replay_task_id,
    attempt: binding.attempt,
    handoff_sha256: binding.handoff_sha256,
    source_archive_sha256: binding.source_archive_sha256,
    execution_profile_digest: binding.execution_profile_digest,
    measurement_config_digest: binding.measurement_config_digest,
    vm_image_digest: binding.vm_image_digest,
  };
}

function historicalProcessBindingFromActive(
  binding: HistoricalPublicActiveBinding,
): HistoricalPublicProcessBinding {
  return {
    ...historicalStatusBindingFromProcess(binding),
    request_id: binding.request_id,
    result_id: binding.result_id,
  };
}

function historicalActiveBinding(
  binding: HistoricalPublicProcessBinding,
  now = Date.now(),
): HistoricalPublicActiveBinding {
  return {
    ...binding,
    cleanup_after_epoch_ms: now + AUTHORITATIVE_CLEANUP_AFTER_MS,
    retained_until_epoch_ms: now + AUTHORITATIVE_TERMINAL_RECEIPT_RETENTION_MS,
  };
}

function historicalActiveBindingValue(
  value: unknown,
): HistoricalPublicActiveBinding | null {
  const binding = objectValue(value);
  if (
    binding === null
    || !exactObjectFields(binding, [
      "schema_version",
      "runner_nonce",
      "replay_task_id",
      "attempt",
      "handoff_sha256",
      "source_archive_sha256",
      "execution_profile_digest",
      "measurement_config_digest",
      "vm_image_digest",
      "request_id",
      "result_id",
      "cleanup_after_epoch_ms",
      "retained_until_epoch_ms",
    ])
    || !Number.isSafeInteger(binding.cleanup_after_epoch_ms)
    || !Number.isSafeInteger(binding.retained_until_epoch_ms)
    || (binding.retained_until_epoch_ms as number)
      - (binding.cleanup_after_epoch_ms as number)
      !== AUTHORITATIVE_TERMINAL_RECEIPT_RETENTION_MS - AUTHORITATIVE_CLEANUP_AFTER_MS
  ) {
    return null;
  }
  return historicalProcessBindingValue({
    schema_version: binding.schema_version,
    runner_nonce: binding.runner_nonce,
    replay_task_id: binding.replay_task_id,
    attempt: binding.attempt,
    handoff_sha256: binding.handoff_sha256,
    source_archive_sha256: binding.source_archive_sha256,
    execution_profile_digest: binding.execution_profile_digest,
    measurement_config_digest: binding.measurement_config_digest,
    vm_image_digest: binding.vm_image_digest,
    request_id: binding.request_id,
    result_id: binding.result_id,
  }) === null
    ? null
    : binding as HistoricalPublicActiveBinding;
}

function sameHistoricalActiveBinding(
  value: unknown,
  request: HistoricalPublicProcessBinding,
): value is HistoricalPublicActiveBinding {
  const binding = historicalActiveBindingValue(value);
  if (binding === null) return false;
  return sameHistoricalProcessBinding(historicalProcessBindingFromActive(binding), request);
}

function rejectHistoricalBindingMismatch(value: unknown): never {
  if (historicalActiveBindingValue(value) === null) {
    throw new ReplayExecutorError("command_output_invalid");
  }
  throw new HistoricalPublicExecutorContractError("runner nonce is already bound");
}

async function claimHistoricalActiveBinding(
  store: TerminalReceiptStore,
  request: HistoricalPublicProcessBinding,
): Promise<void> {
  let value: unknown;
  try {
    value = await store.claimBinding(historicalActiveBinding(request));
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  if (!sameHistoricalActiveBinding(value, request)) rejectHistoricalBindingMismatch(value);
}

async function requireHistoricalActiveBinding(
  store: TerminalReceiptStore,
  request: HistoricalPublicExecutorStatusRequest,
): Promise<HistoricalPublicProcessBinding> {
  let value: unknown;
  try {
    value = await store.readBinding();
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  if (value === null) throw new ReplayExecutorError("command_rpc_failed");
  const binding = historicalActiveBindingValue(value);
  if (binding === null) throw new ReplayExecutorError("command_output_invalid");
  const processBinding = historicalProcessBindingFromActive(binding);
  if (!sameHistoricalStatusBinding(
    historicalStatusBindingFromProcess(processBinding),
    request,
  )) {
    throw new HistoricalPublicExecutorContractError("runner nonce is already bound");
  }
  return processBinding;
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

function validateHistoricalTerminalReceipt(
  value: unknown,
  request: HistoricalPublicProcessBinding,
): HistoricalPublicTerminalReceipt {
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
    || !sameHistoricalProcessBinding(receipt.binding, request)
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
      !exactObjectFields(body, [
        "schema_version",
        "contract",
        "runner_nonce",
        "replay_task_id",
        "attempt",
        "handoff_sha256",
        "source_archive_sha256",
        "execution_profile_digest",
        "measurement_config_digest",
        "vm_image_digest",
        "runner_verdict",
        "destruction",
      ])
      || body.contract !== "historical_public_executor_v1"
      || body.destruction !== "confirmed"
      || !sameHistoricalStatusBinding({
        schema_version: body.schema_version,
        runner_nonce: body.runner_nonce,
        replay_task_id: body.replay_task_id,
        attempt: body.attempt,
        handoff_sha256: body.handoff_sha256,
        source_archive_sha256: body.source_archive_sha256,
        execution_profile_digest: body.execution_profile_digest,
        measurement_config_digest: body.measurement_config_digest,
        vm_image_digest: body.vm_image_digest,
      }, historicalStatusBindingFromProcess(request))
    ) {
      throw new ReplayExecutorError("command_output_invalid");
    }
    let verdict: HistoricalPublicExecutorVerdict;
    try {
      verdict = historicalPublicExecutorVerdictFromBinding(
        historicalStatusBindingFromProcess(request),
        { request_id: request.request_id, result_id: request.result_id },
        body.runner_verdict,
      );
    } catch {
      throw new ReplayExecutorError("command_output_invalid");
    }
    return {
      schema_version: 1,
      binding: { ...request },
      http_status: 200,
      body: verdict,
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

async function readHistoricalTerminalReceipt(
  store: TerminalReceiptStore,
  request: HistoricalPublicProcessBinding,
): Promise<HistoricalPublicTerminalReceipt | null> {
  let value: unknown;
  try {
    value = await store.readReceipt();
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  return value === null ? null : validateHistoricalTerminalReceipt(value, request);
}

async function prepareHistoricalTerminalReceipt(
  store: TerminalReceiptStore,
  receipt: HistoricalPublicTerminalReceipt,
  request: HistoricalPublicProcessBinding,
): Promise<HistoricalPublicTerminalReceipt> {
  let value: unknown;
  try {
    value = await store.prepareReceipt(receipt);
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  return validateHistoricalTerminalReceipt(value, request);
}

function historicalTerminalReceiptResponse(
  receipt: HistoricalPublicTerminalReceipt,
): Response {
  if (receipt.destruction_state !== "confirmed") {
    throw new ReplayExecutorError("sandbox_destroy_failed");
  }
  return json(receipt.body, receipt.http_status);
}

async function confirmHistoricalTerminalReceipt(
  sandbox: SandboxClient,
  store: TerminalReceiptStore,
  receipt: HistoricalPublicTerminalReceipt,
): Promise<HistoricalPublicTerminalReceipt> {
  if (receipt.destruction_state === "confirmed") return receipt;
  try {
    await sandbox.destroy();
  } catch {
    throw new ReplayExecutorError("sandbox_destroy_failed");
  }
  let value: unknown;
  try {
    value = await store.confirmReceipt();
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  return validateHistoricalTerminalReceipt(value, receipt.binding);
}

function historicalTerminalReceipt(
  request: HistoricalPublicProcessBinding,
  status: string,
  logs: { stdout: string; stderr: string },
  now = Date.now(),
): HistoricalPublicTerminalReceipt {
  let httpStatus: 200 | 500;
  let body: HistoricalPublicTerminalReceipt["body"];
  try {
    if (status !== "completed") {
      throw new ReplayExecutorError("command_failed");
    }
    if (logs.stdout.length > 64 * 1024) {
      throw new ReplayExecutorError("command_output_invalid");
    }
    try {
      body = historicalPublicExecutorVerdictFromBinding(
        historicalStatusBindingFromProcess(request),
        { request_id: request.request_id, result_id: request.result_id },
        JSON.parse(logs.stdout) as unknown,
      );
    } catch {
      throw new ReplayExecutorError("command_output_invalid");
    }
    httpStatus = 200;
  } catch (error) {
    recordExecutorFailure("historical_public_replay_status", error);
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

function historicalRunningResponse(
  request: HistoricalPublicExecutorStatusRequest,
): Response {
  return json({
    schema_version: 1,
    replay_task_id: request.replay_task_id,
    attempt: request.attempt,
    status: "running",
  }, 202);
}

type HistoricalCleanupIdentity = {
  schema_version: 1;
  replay_task_id: string;
  attempt: number;
};

function validateHistoricalCleanupIdentity(value: unknown): HistoricalCleanupIdentity {
  const identity = objectValue(value);
  if (
    identity === null
    || !exactObjectFields(identity, ["schema_version", "replay_task_id", "attempt"])
    || identity.schema_version !== 1
    || typeof identity.replay_task_id !== "string"
    || !REPLAY_TASK_ID.test(identity.replay_task_id)
    || !Number.isSafeInteger(identity.attempt)
    || (identity.attempt as number) < 1
    || (identity.attempt as number) > MAX_REPLAY_ATTEMPTS
  ) {
    throw new HistoricalPublicExecutorContractError("cleanup identity is invalid");
  }
  return {
    schema_version: 1,
    replay_task_id: identity.replay_task_id,
    attempt: identity.attempt as number,
  };
}

function validateHistoricalCleanupConfirmation(
  value: unknown,
  expected: HistoricalCleanupIdentity,
): void {
  const marker = objectValue(value);
  const exactTombstone = marker !== null && exactObjectFields(marker, [
    "schema_version",
    "replay_task_id",
    "attempt",
    "destruction_state",
  ]);
  const exactRetainedConfirmation = marker !== null && exactObjectFields(marker, [
    "schema_version",
    "replay_task_id",
    "attempt",
    "destruction_state",
    "confirmed_at_epoch_ms",
    "retained_until_epoch_ms",
  ]);
  if (
    marker === null
    || (!exactTombstone && !exactRetainedConfirmation)
    || marker.schema_version !== expected.schema_version
    || marker.replay_task_id !== expected.replay_task_id
    || marker.attempt !== expected.attempt
    || marker.destruction_state !== "confirmed"
    || (
      exactRetainedConfirmation
      && (
        !Number.isSafeInteger(marker.confirmed_at_epoch_ms)
        || !Number.isSafeInteger(marker.retained_until_epoch_ms)
        || (marker.retained_until_epoch_ms as number) <= (marker.confirmed_at_epoch_ms as number)
      )
    )
  ) {
    throw new ReplayExecutorError("command_output_invalid");
  }
}

async function historicalProcessStatus(
  sandbox: SandboxClient,
  store: TerminalReceiptStore,
  request: HistoricalPublicProcessBinding,
): Promise<Response> {
  const stored = await readHistoricalTerminalReceipt(store, request);
  if (stored !== null) {
    return historicalTerminalReceiptResponse(
      await confirmHistoricalTerminalReceipt(sandbox, store, stored),
    );
  }
  if (sandbox.getProcess === undefined) throw new ReplayExecutorError("command_rpc_failed");
  let process: Awaited<ReturnType<NonNullable<SandboxClient["getProcess"]>>>;
  try {
    process = await sandbox.getProcess(HISTORICAL_PUBLIC_PROCESS_ID);
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
    return historicalRunningResponse(historicalStatusBindingFromProcess(request));
  }
  let logs: Awaited<ReturnType<typeof process.getLogs>>;
  try {
    logs = await process.getLogs();
  } catch {
    throw new ReplayExecutorError("command_rpc_failed");
  }
  const receipt = await prepareHistoricalTerminalReceipt(
    store,
    historicalTerminalReceipt(request, status, logs),
    request,
  );
  return historicalTerminalReceiptResponse(
    await confirmHistoricalTerminalReceipt(sandbox, store, receipt),
  );
}

async function writeSandboxFile(
  sandbox: SandboxClient,
  path: string,
  contents: string,
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
    historical_public_replay_enabled: env.HISTORICAL_PUBLIC_REPLAY_ENABLED === "true",
    staging_acceptance_enabled: env.STAGING_ACCEPTANCE_ENABLED === "true",
    staging_memory_limit_bytes: Number(env.STAGING_MEMORY_LIMIT_BYTES),
    production_memory_gate_bytes: Number(env.PRODUCTION_MEMORY_GATE_BYTES),
    reviewed_execution_profile_digest: env.REVIEWED_EXECUTION_PROFILE_DIGEST,
    reviewed_measurement_config_digest: env.REVIEWED_MEASUREMENT_CONFIG_DIGEST,
    reviewed_vm_image_digest: env.REVIEWED_VM_IMAGE_DIGEST,
    ...(env.EXECUTOR_OWNERSHIP_TAG === undefined ? {} : {
      executor_ownership_tag: env.EXECUTOR_OWNERSHIP_TAG,
      expected_replay_task_id: env.EXPECTED_REPLAY_TASK_ID,
      expected_replay_attempt: env.EXPECTED_REPLAY_ATTEMPT,
    }),
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
  const historicalPublicReplay = url.pathname === "/api/v1/historical-public-replay";
  const historicalPublicStatus = url.pathname === "/api/v1/historical-public-replay/status";
  const historicalPublicCleanup = url.pathname === "/api/v1/historical-public-replay/cleanup";
  const historicalPublicReservation = url.pathname
    === "/api/v1/historical-public-replay/cleanup-reservation";
  if (
    (
      !syntheticAcceptance
      && !archiveAcceptance
      && !authoritativeReplay
      && !authoritativeStatus
      && !historicalPublicReplay
      && !historicalPublicStatus
      && !historicalPublicCleanup
      && !historicalPublicReservation
    ) ||
    request.method !== "POST"
  ) {
    return json({ error: "not_found" }, 404);
  }
  if (authoritativeReplay && env.REPLAY_ENABLED !== "true") {
    return json({ error: "replay_disabled" }, 503);
  }
  if (
    (
      historicalPublicReplay
      || historicalPublicStatus
      || historicalPublicCleanup
      || historicalPublicReservation
    )
    && env.HISTORICAL_PUBLIC_REPLAY_ENABLED !== "true"
  ) {
    return json({ error: "historical_public_replay_disabled" }, 503);
  }
  if (historicalPublicReservation) {
    try {
      await dependencies.authenticate(request, env);
      if (dependencies.recoveryStore === undefined) {
        throw new ReplayExecutorError("command_rpc_failed");
      }
      const identity = validateHistoricalCleanupIdentity(await request.json());
      const store = dependencies.recoveryStore(
        env,
        identity.replay_task_id,
        identity.attempt,
      );
      const reserved = await store.reserveCleanupIdentity(identity);
      const confirmed = validateHistoricalCleanupIdentity(reserved);
      if (
        confirmed.replay_task_id !== identity.replay_task_id
        || confirmed.attempt !== identity.attempt
      ) {
        throw new ReplayExecutorError("command_output_invalid");
      }
      return json({ ...identity, status: "reserved" });
    } catch (error) {
      if (error instanceof ReplayAuthError) return json({ error: "unauthorized" }, 401);
      if (error instanceof HistoricalPublicExecutorContractError || error instanceof SyntaxError) {
        return json({ error: "invalid_request" }, 400);
      }
      recordExecutorFailure("historical_public_replay_cleanup_reservation", error);
      return authoritativeExecutorFailure(error);
    }
  }
  if (historicalPublicCleanup) {
    try {
      await dependencies.authenticate(request, env);
      if (dependencies.recoveryStore === undefined) {
        throw new ReplayExecutorError("command_rpc_failed");
      }
      const identity = validateHistoricalCleanupIdentity(await request.json());
      const store = dependencies.recoveryStore(
        env,
        identity.replay_task_id,
        identity.attempt,
      );
      const marker = await store.destroyBoundSandbox(identity);
      validateHistoricalCleanupConfirmation(marker, identity);
      return json({ ...identity, destruction: "confirmed" });
    } catch (error) {
      if (error instanceof ReplayAuthError) return json({ error: "unauthorized" }, 401);
      if (error instanceof HistoricalPublicExecutorContractError || error instanceof SyntaxError) {
        return json({ error: "invalid_request" }, 400);
      }
      recordExecutorFailure("historical_public_replay_cleanup", error);
      return authoritativeExecutorFailure(error);
    }
  }
  if (historicalPublicStatus) {
    try {
      await dependencies.authenticate(request, env);
      const input = await readHistoricalPublicExecutorStatusRequest(
        request,
        env.REVIEWED_EXECUTION_PROFILE_DIGEST,
        env.REVIEWED_MEASUREMENT_CONFIG_DIGEST,
        env.REVIEWED_VM_IMAGE_DIGEST,
      );
      const store = terminalReceiptStore(dependencies, env, input.runner_nonce, input);
      const binding = await requireHistoricalActiveBinding(store, input);
      const sandbox = dependencies.sandbox(env, input.runner_nonce);
      return await historicalProcessStatus(sandbox, store, binding);
    } catch (error) {
      if (error instanceof ReplayAuthError) return json({ error: "unauthorized" }, 401);
      if (error instanceof HistoricalPublicExecutorContractError || error instanceof SyntaxError) {
        return json({ error: "invalid_request" }, 400);
      }
      recordExecutorFailure("historical_public_replay_status", error);
      return authoritativeExecutorFailure(error);
    }
  }
  if (historicalPublicReplay) {
    try {
      await dependencies.authenticate(request, env);
      const input = await readHistoricalPublicExecutorRequest(
        request,
        env.REVIEWED_EXECUTION_PROFILE_DIGEST,
        env.REVIEWED_MEASUREMENT_CONFIG_DIGEST,
        env.REVIEWED_VM_IMAGE_DIGEST,
      );
      const store = terminalReceiptStore(
        dependencies,
        env,
        input.runner_nonce,
        historicalStatusBinding(input),
      );
      const binding = historicalProcessBinding(input);
      await claimHistoricalActiveBinding(store, binding);
      const existingReceipt = await readHistoricalTerminalReceipt(store, binding);
      if (existingReceipt !== null) {
        return historicalRunningResponse(historicalStatusBinding(input));
      }
      const sandbox = dependencies.sandbox(env, input.runner_nonce);
      try {
        await startHistoricalPublicProcess(sandbox, async () => {
          await writeSandboxFile(
            sandbox,
            "/workspace/historical-public-request.json",
            canonicalHistoricalPublicHandoff(input.handoff),
          );
          await writeSandboxFile(
            sandbox,
            "/workspace/historical-public-source.tar.gz.b64",
            input.source_archive_base64,
          );
        });
      } catch (error) {
        if (!(error instanceof ProcessStartConflictError)) {
          await sandbox.destroy();
        }
        throw error;
      }
      return historicalRunningResponse(historicalStatusBinding(input));
    } catch (error) {
      if (error instanceof ReplayAuthError) return json({ error: "unauthorized" }, 401);
      if (error instanceof HistoricalPublicExecutorContractError || error instanceof SyntaxError) {
        return json({ error: "invalid_request" }, 400);
      }
      recordExecutorFailure("historical_public_replay", error);
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
      requireHistoricalPrivateBinding(env, input);
      const store = terminalReceiptStore(dependencies, env, input.runner_nonce);
      await requireActiveBinding(store, input);
      sandbox = dependencies.sandbox(env, input.runner_nonce);
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
      requireHistoricalPrivateBinding(env, {
        replay_task_id: String(input.request.replay_task_id),
        attempt: Number(input.request.attempt),
      });
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
      const sandbox = dependencies.sandbox(env, input.runner_nonce);
      try {
        await startAuthoritativeProcess(sandbox, async () => {
          await writeSandboxFile(sandbox, "/workspace/replay-request.json", JSON.stringify(input.request));
          await writeSandboxFile(
            sandbox,
            "/workspace/archive-expectation.json",
            JSON.stringify(input.archive_expectation),
          );
          await writeSandboxFile(sandbox, "/workspace/archive.tar.gz.age.b64", input.ciphertext_base64);
          if (input.schema_version === 1) {
            await writeSandboxFile(sandbox, "/workspace/identity.age.b64", input.plaintext_identity_base64);
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
      if (error instanceof AuthoritativeReplayContractError || error instanceof SyntaxError) {
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
      const sandbox = dependencies.sandbox(env, input.runner_nonce);
      const evidence = await withSandboxDestruction(sandbox, async () => {
          await writeSandboxFile(sandbox, "/workspace/archive.tar.gz.age.b64", input.ciphertext_base64);
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
    const sandbox = dependencies.sandbox(env, input.runner_nonce);
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
