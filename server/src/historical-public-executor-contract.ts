const DIGEST = /^[0-9a-f]{64}$/;
const OCI_DIGEST = /^sha256:[0-9a-f]{64}$/;
const REPLAY_ID = /^rt1_[0-9a-f]{64}$/;
const REQUEST_ID = /^prr_[0-9a-f]{64}$/;
const RESULT_ID = /^r2_[0-9a-f]{64}$/;
const BASE64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

const ZERO_DIGEST = "0".repeat(64);
const MAX_REQUEST_BYTES = 24 * 1024 * 1024;
const MAX_SOURCE_ARCHIVE_BYTES = 16 * 1024 * 1024;
export const MAX_REPLAY_ATTEMPTS = 4;

export type HistoricalPublicRunnerVerdict = {
  schema_version: 1;
  request_id: string;
  result_id: string;
  execution_outcome: "completed" | "crashed" | "timed_out";
  checker_outcome: "accepted" | "rejected" | "declined" | null;
  failure_reason: null;
  statistics: Record<string, unknown>;
};

export type HistoricalPublicExecutorInput = {
  schema_version: 1;
  runner_nonce: string;
  replay_task_id: string;
  attempt: number;
  handoff_sha256: string;
  source_archive_sha256: string;
  execution_profile_digest: string;
  measurement_config_digest: string;
  vm_image_digest: string;
  handoff: Record<string, unknown>;
  source_archive_base64: string;
};

export type HistoricalPublicExecutorStatusRequest = Omit<
  HistoricalPublicExecutorInput,
  "handoff" | "source_archive_base64"
>;

export type HistoricalPublicExecutorVerdict = HistoricalPublicExecutorStatusRequest & {
  contract: "historical_public_executor_v1";
  runner_verdict: HistoricalPublicRunnerVerdict;
  destruction: "confirmed";
};

export type HistoricalPublicRunnerBinding = {
  request_id: string;
  result_id: string;
};

export class HistoricalPublicExecutorContractError extends Error {}

function object(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new HistoricalPublicExecutorContractError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactFields(value: Record<string, unknown>, fields: readonly string[], label: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((field, index) => field !== expected[index])) {
    throw new HistoricalPublicExecutorContractError(`${label} fields are not closed`);
  }
}

function text(value: unknown, label: string, maximum: number): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum) {
    throw new HistoricalPublicExecutorContractError(`${label} is invalid`);
  }
  return value;
}

function positiveSafeInteger(value: unknown, label: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) {
    throw new HistoricalPublicExecutorContractError(`${label} is invalid`);
  }
  return value as number;
}

function nonnegativeSafeInteger(value: unknown, label: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    throw new HistoricalPublicExecutorContractError(`${label} is invalid`);
  }
  return value as number;
}

function canonicalDocument(value: unknown): string {
  const render = (item: unknown, indent: number): string => {
    if (item === null) return "null";
    if (typeof item === "boolean" || typeof item === "number") return JSON.stringify(item);
    if (typeof item === "string") return JSON.stringify(item);
    if (Array.isArray(item)) {
      if (item.length === 0) return "[]";
      const prefix = " ".repeat(indent + 2);
      return `[\n${item.map((entry) => `${prefix}${render(entry, indent + 2)}`).join(",\n")}\n${" ".repeat(indent)}]`;
    }
    const record = object(item, "canonical JSON value");
    const entries = Object.keys(record).sort();
    if (entries.length === 0) return "{}";
    const prefix = " ".repeat(indent + 2);
    return `{\n${entries.map((key) => `${prefix}${JSON.stringify(key)}: ${render(record[key], indent + 2)}`).join(",\n")}\n${" ".repeat(indent)}}`;
  };
  return `${render(value, 0)}\n`;
}

async function sha256Bytes(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function sha256Text(value: string): Promise<string> {
  return sha256Bytes(new TextEncoder().encode(value));
}

function decodeCanonicalBase64(value: unknown): Uint8Array {
  const encoded = text(value, "source_archive_base64", Math.ceil(MAX_SOURCE_ARCHIVE_BYTES / 3) * 4);
  if (!BASE64.test(encoded)) {
    throw new HistoricalPublicExecutorContractError("source archive is not canonical base64");
  }
  const padding = encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0;
  const decodedLength = encoded.length / 4 * 3 - padding;
  if (decodedLength < 1 || decodedLength > MAX_SOURCE_ARCHIVE_BYTES) {
    throw new HistoricalPublicExecutorContractError("source archive exceeds its size limit");
  }
  return Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
}

function requireReviewedDigests(
  reviewedExecutionProfileDigest: string,
  reviewedMeasurementConfigDigest: string,
  reviewedVmImageDigest: string,
): void {
  if (
    !DIGEST.test(reviewedExecutionProfileDigest)
    || !DIGEST.test(reviewedMeasurementConfigDigest)
    || !OCI_DIGEST.test(reviewedVmImageDigest)
    || reviewedExecutionProfileDigest === ZERO_DIGEST
    || reviewedMeasurementConfigDigest === ZERO_DIGEST
    || reviewedVmImageDigest === `sha256:${ZERO_DIGEST}`
  ) {
    throw new HistoricalPublicExecutorContractError("reviewed historical execution is not configured");
  }
}

function validateIdentity(
  value: Record<string, unknown>,
  reviewedExecutionProfileDigest: string,
  reviewedMeasurementConfigDigest: string,
  reviewedVmImageDigest: string,
): HistoricalPublicExecutorStatusRequest {
  const runnerNonce = text(value.runner_nonce, "runner_nonce", 64);
  const replayTaskId = text(value.replay_task_id, "replay_task_id", 68);
  const attempt = positiveSafeInteger(value.attempt, "attempt");
  const handoffDigest = text(value.handoff_sha256, "handoff_sha256", 64);
  const archiveDigest = text(value.source_archive_sha256, "source_archive_sha256", 64);
  if (
    value.schema_version !== 1
    || attempt > MAX_REPLAY_ATTEMPTS
    || !DIGEST.test(runnerNonce)
    || !REPLAY_ID.test(replayTaskId)
    || !DIGEST.test(handoffDigest)
    || !DIGEST.test(archiveDigest)
    || value.execution_profile_digest !== reviewedExecutionProfileDigest
    || value.measurement_config_digest !== reviewedMeasurementConfigDigest
    || value.vm_image_digest !== reviewedVmImageDigest
  ) {
    throw new HistoricalPublicExecutorContractError("historical execution identity is invalid");
  }
  return {
    schema_version: 1,
    runner_nonce: runnerNonce,
    replay_task_id: replayTaskId,
    attempt,
    handoff_sha256: handoffDigest,
    source_archive_sha256: archiveDigest,
    execution_profile_digest: reviewedExecutionProfileDigest,
    measurement_config_digest: reviewedMeasurementConfigDigest,
    vm_image_digest: reviewedVmImageDigest,
  };
}

async function readJsonRequest(incoming: Request, maximumBytes: number, label: string): Promise<Record<string, unknown>> {
  const contentLength = incoming.headers.get("content-length");
  if (contentLength !== null && (!/^\d+$/.test(contentLength) || Number(contentLength) > maximumBytes)) {
    throw new HistoricalPublicExecutorContractError(`${label} exceeds the size limit`);
  }
  const bytes = new Uint8Array(await incoming.arrayBuffer());
  if (bytes.length === 0 || bytes.length > maximumBytes) {
    throw new HistoricalPublicExecutorContractError(`${label} exceeds the size limit`);
  }
  try {
    return object(JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes)), label);
  } catch (error) {
    if (error instanceof HistoricalPublicExecutorContractError) throw error;
    throw new HistoricalPublicExecutorContractError(`${label} is not one UTF-8 JSON object`);
  }
}

export async function readHistoricalPublicExecutorRequest(
  incoming: Request,
  reviewedExecutionProfileDigest: string,
  reviewedMeasurementConfigDigest: string,
  reviewedVmImageDigest: string,
): Promise<HistoricalPublicExecutorInput> {
  requireReviewedDigests(
    reviewedExecutionProfileDigest,
    reviewedMeasurementConfigDigest,
    reviewedVmImageDigest,
  );
  const value = await readJsonRequest(incoming, MAX_REQUEST_BYTES, "historical executor request");
  exactFields(value, [
    "schema_version",
    "runner_nonce",
    "replay_task_id",
    "attempt",
    "handoff_sha256",
    "source_archive_sha256",
    "execution_profile_digest",
    "measurement_config_digest",
    "vm_image_digest",
    "handoff",
    "source_archive_base64",
  ], "historical executor request");
  const identity = validateIdentity(
    value,
    reviewedExecutionProfileDigest,
    reviewedMeasurementConfigDigest,
    reviewedVmImageDigest,
  );
  const handoff = object(value.handoff, "historical runner handoff");
  if (
    await sha256Text(canonicalDocument(handoff)) !== identity.handoff_sha256
    || handoff.request_id === undefined
    || !REQUEST_ID.test(text(handoff.request_id, "handoff request_id", 68))
  ) {
    throw new HistoricalPublicExecutorContractError("historical runner handoff digest is invalid");
  }
  const result = object(handoff.result, "historical runner handoff result");
  if (!RESULT_ID.test(text(result.result_id, "handoff result_id", 67))) {
    throw new HistoricalPublicExecutorContractError("historical runner handoff result is invalid");
  }
  const archive = decodeCanonicalBase64(value.source_archive_base64);
  if (await sha256Bytes(archive) !== identity.source_archive_sha256) {
    throw new HistoricalPublicExecutorContractError("source archive digest differs from the request");
  }
  const source = object(handoff.source, "historical runner handoff source");
  if (
    source.archive_sha256 !== identity.source_archive_sha256
    || source.archive_size_bytes !== archive.byteLength
  ) {
    throw new HistoricalPublicExecutorContractError("source archive differs from the runner handoff");
  }
  return {
    ...identity,
    handoff,
    source_archive_base64: value.source_archive_base64 as string,
  };
}

export async function readHistoricalPublicExecutorStatusRequest(
  incoming: Request,
  reviewedExecutionProfileDigest: string,
  reviewedMeasurementConfigDigest: string,
  reviewedVmImageDigest: string,
): Promise<HistoricalPublicExecutorStatusRequest> {
  requireReviewedDigests(
    reviewedExecutionProfileDigest,
    reviewedMeasurementConfigDigest,
    reviewedVmImageDigest,
  );
  const value = await readJsonRequest(incoming, 4096, "historical executor status request");
  exactFields(value, [
    "schema_version",
    "runner_nonce",
    "replay_task_id",
    "attempt",
    "handoff_sha256",
    "source_archive_sha256",
    "execution_profile_digest",
    "measurement_config_digest",
    "vm_image_digest",
  ], "historical executor status request");
  return validateIdentity(
    value,
    reviewedExecutionProfileDigest,
    reviewedMeasurementConfigDigest,
    reviewedVmImageDigest,
  );
}

function validateCounter(value: unknown, label: string): Record<string, unknown> {
  const counter = object(value, label);
  if (counter.status === "measured") {
    exactFields(counter, ["status", "value"], label);
    nonnegativeSafeInteger(counter.value, `${label}.value`);
  } else if (counter.status === "unavailable") {
    exactFields(counter, ["status", "reason"], label);
    if (!["counter_not_reported", "counter_not_supported", "counter_permission_denied"].includes(counter.reason as string)) {
      throw new HistoricalPublicExecutorContractError(`${label}.reason is invalid`);
    }
  } else {
    throw new HistoricalPublicExecutorContractError(`${label}.status is invalid`);
  }
  return counter;
}

export function validateHistoricalPublicRunnerVerdict(
  value: unknown,
  input: HistoricalPublicExecutorInput,
): HistoricalPublicRunnerVerdict {
  return validateHistoricalPublicRunnerVerdictBinding(
    value,
    historicalPublicRunnerBinding(input),
  );
}

function validateHistoricalPublicRunnerVerdictBinding(
  value: unknown,
  binding: HistoricalPublicRunnerBinding,
): HistoricalPublicRunnerVerdict {
  const verdict = object(value, "historical runner verdict");
  exactFields(verdict, [
    "schema_version",
    "request_id",
    "result_id",
    "execution_outcome",
    "checker_outcome",
    "failure_reason",
    "statistics",
  ], "historical runner verdict");
  if (
    verdict.schema_version !== 1
    || verdict.request_id !== binding.request_id
    || verdict.result_id !== binding.result_id
    || !["completed", "crashed", "timed_out"].includes(verdict.execution_outcome as string)
    || (verdict.execution_outcome === "completed"
      ? !["accepted", "rejected", "declined"].includes(verdict.checker_outcome as string)
      : verdict.checker_outcome !== null)
    || verdict.failure_reason !== null
  ) {
    throw new HistoricalPublicExecutorContractError("historical runner verdict differs from the request");
  }
  const statistics = object(verdict.statistics, "historical runner verdict statistics");
  exactFields(statistics, [
    "checker_wall_time_ms",
    "checker_retired_instructions",
    "build_wall_time_ms",
    "build_retired_instructions",
    "lines_of_code",
    "file_count",
  ], "historical runner verdict statistics");
  for (const field of ["checker_wall_time_ms", "build_wall_time_ms", "lines_of_code", "file_count"] as const) {
    nonnegativeSafeInteger(statistics[field], `historical runner verdict ${field}`);
  }
  validateCounter(statistics.checker_retired_instructions, "historical checker counter");
  validateCounter(statistics.build_retired_instructions, "historical build counter");
  return verdict as HistoricalPublicRunnerVerdict;
}

export function historicalPublicRunnerBinding(
  input: HistoricalPublicExecutorInput,
): HistoricalPublicRunnerBinding {
  const result = object(input.handoff.result, "historical runner handoff result");
  return {
    request_id: input.handoff.request_id as string,
    result_id: result.result_id as string,
  };
}

export function historicalPublicExecutorVerdictFromBinding(
  input: HistoricalPublicExecutorStatusRequest,
  binding: HistoricalPublicRunnerBinding,
  runnerVerdict: unknown,
): HistoricalPublicExecutorVerdict {
  return {
    ...input,
    contract: "historical_public_executor_v1",
    runner_verdict: validateHistoricalPublicRunnerVerdictBinding(runnerVerdict, binding),
    destruction: "confirmed",
  };
}

export function historicalPublicExecutorVerdict(
  input: HistoricalPublicExecutorInput,
  runnerVerdict: unknown,
): HistoricalPublicExecutorVerdict {
  return historicalPublicExecutorVerdictFromBinding(
    {
      schema_version: 1,
      runner_nonce: input.runner_nonce,
      replay_task_id: input.replay_task_id,
      attempt: input.attempt,
      handoff_sha256: input.handoff_sha256,
      source_archive_sha256: input.source_archive_sha256,
      execution_profile_digest: input.execution_profile_digest,
      measurement_config_digest: input.measurement_config_digest,
      vm_image_digest: input.vm_image_digest,
    },
    historicalPublicRunnerBinding(input),
    runnerVerdict,
  );
}

export function canonicalHistoricalPublicHandoff(value: unknown): string {
  return canonicalDocument(value);
}
