const DIGEST = /^[0-9a-f]{64}$/;
const ZERO_DIGEST = "0".repeat(64);
const UUID7 = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const REPLAY_ID = /^rt1_[0-9a-f]{64}$/;
const BASE64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

// The archive no longer travels in this body; it is uploaded in parts and
// assembled in the Sandbox before this request is made. What remains is the
// execution request and the key material, both small, so the body bound is now
// a few hundred kilobytes rather than a few megabytes.
const MAX_REQUEST_BYTES = 256 * 1024;
const MAX_IDENTITY_BYTES = 4096;
const MAX_PLAINTEXT_BYTES = 10 * 1024 * 1024;
const MAX_REPLAY_ATTEMPTS = 4;
const MAX_ARCHIVE_PART_COUNT = 32;

/**
 * The transport version and the archive key-envelope version are separate.
 * Envelope v1 wraps an age identity and v2 a 16-byte file key; the transport
 * carries either, named explicitly, so widening the transport does not force a
 * new envelope version on archives that were sealed years earlier.
 */
export type ReplayKeyMaterialType = "age-identity-v1" | "age-file-key-v1";

export type AuthoritativeReplayInput = {
  schema_version: 3;
  runner_nonce: string;
  request: Record<string, unknown>;
  archive_expectation: {
    schema_version: 1 | 2;
    submission_id: string;
    archive_ciphertext_sha256: string;
    plaintext_tar_sha256: string;
    plaintext_tar_size: number;
    key_material_type?: "age-file-key-v1";
  };
  archive_ciphertext_bytes: number;
  archive_part_count: number;
  key_material_type: ReplayKeyMaterialType;
  plaintext_key_material_base64: string;
};

export type ReplayVerdict = {
  schema_version: 1;
  replay_task_id: string;
  attempt: number;
  execution_outcome: "completed" | "crashed" | "timed_out";
  checker_outcome: "accepted" | "rejected" | "declined" | null;
  failure_reason: null;
  statistics: Record<string, unknown>;
};

export type AuthoritativeReplayStatusRequest = {
  schema_version: 1;
  runner_nonce: string;
  replay_task_id: string;
  attempt: number;
  execution_profile_digest: string;
  measurement_config_digest: string;
  vm_image_digest: string;
};

export class AuthoritativeReplayContractError extends Error {}

function object(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new AuthoritativeReplayContractError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactFields(value: Record<string, unknown>, fields: readonly string[], label: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((field, index) => field !== expected[index])) {
    throw new AuthoritativeReplayContractError(`${label} fields are not canonical`);
  }
}

function text(value: unknown, label: string, maximum: number): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum) {
    throw new AuthoritativeReplayContractError(`${label} is invalid`);
  }
  return value;
}

function safeInteger(value: unknown, label: string, maximum: number): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1 || (value as number) > maximum) {
    throw new AuthoritativeReplayContractError(`${label} is invalid`);
  }
  return value as number;
}

function nonnegativeSafeInteger(value: unknown, label: string): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    throw new AuthoritativeReplayContractError(`${label} is invalid`);
  }
  return value as number;
}

function canonicalBase64(value: unknown, label: string, maximumBytes: number): string {
  const encoded = text(value, label, Math.ceil(maximumBytes / 3) * 4);
  if (!BASE64.test(encoded)) {
    throw new AuthoritativeReplayContractError(`${label} is not canonical base64`);
  }
  const padding = encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0;
  const decodedLength = encoded.length / 4 * 3 - padding;
  if (decodedLength < 1 || decodedLength > maximumBytes) {
    throw new AuthoritativeReplayContractError(`${label} exceeds its decoded size limit`);
  }
  return encoded;
}


function requireReviewedDigests(
  reviewedProfileDigest: string,
  reviewedMeasurementDigest: string,
  reviewedVmImageDigest: string,
): void {
  if (
    !DIGEST.test(reviewedProfileDigest) ||
    !DIGEST.test(reviewedMeasurementDigest) ||
    !/^sha256:[0-9a-f]{64}$/.test(reviewedVmImageDigest) ||
    reviewedProfileDigest === ZERO_DIGEST ||
    reviewedMeasurementDigest === ZERO_DIGEST ||
    reviewedVmImageDigest === `sha256:${ZERO_DIGEST}`
  ) {
    throw new AuthoritativeReplayContractError("reviewed replay digests are not configured");
  }
}

export async function readAuthoritativeReplayRequest(
  incoming: Request,
  reviewedProfileDigest: string,
  reviewedMeasurementDigest: string,
  reviewedVmImageDigest: string,
): Promise<AuthoritativeReplayInput> {
  requireReviewedDigests(
    reviewedProfileDigest,
    reviewedMeasurementDigest,
    reviewedVmImageDigest,
  );
  const contentLength = incoming.headers.get("content-length");
  if (contentLength !== null && (!/^\d+$/.test(contentLength) || Number(contentLength) > MAX_REQUEST_BYTES)) {
    throw new AuthoritativeReplayContractError("request exceeds the size limit");
  }
  const bytes = new Uint8Array(await incoming.arrayBuffer());
  if (bytes.length === 0 || bytes.length > MAX_REQUEST_BYTES) {
    throw new AuthoritativeReplayContractError("request exceeds the size limit");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes));
  } catch {
    throw new AuthoritativeReplayContractError("request is not one UTF-8 JSON object");
  }
  const outer = object(parsed, "request");
  if (outer.schema_version !== 3) {
    throw new AuthoritativeReplayContractError("request schema_version must be integer 3");
  }
  exactFields(outer, [
    "schema_version",
    "runner_nonce",
    "request",
    "archive_expectation",
    "archive_ciphertext_bytes",
    "archive_part_count",
    "key_material_type",
    "plaintext_key_material_base64",
  ], "request");
  const runnerNonce = text(outer.runner_nonce, "runner_nonce", 64);
  if (!DIGEST.test(runnerNonce)) throw new AuthoritativeReplayContractError("runner_nonce is invalid");
  const execution = object(outer.request, "execution request");
  const replayTaskId = text(execution.replay_task_id, "replay_task_id", 68);
  if (!REPLAY_ID.test(replayTaskId)) throw new AuthoritativeReplayContractError("replay_task_id is invalid");
  safeInteger(execution.attempt, "attempt", MAX_REPLAY_ATTEMPTS);
  if (execution.execution_profile_digest !== reviewedProfileDigest) {
    throw new AuthoritativeReplayContractError("execution profile is not reviewed");
  }
  if (execution.measurement_config_digest !== reviewedMeasurementDigest) {
    throw new AuthoritativeReplayContractError("measurement configuration is not reviewed");
  }
  const executionProfile = object(execution.execution_profile, "execution profile");
  if (executionProfile.vm_image_digest !== reviewedVmImageDigest) {
    throw new AuthoritativeReplayContractError("VM image is not reviewed");
  }
  const source = object(execution.source, "execution source");
  if (source.visibility !== "private") {
    throw new AuthoritativeReplayContractError("authoritative endpoint requires private source");
  }
  const archive = object(source.archive, "execution archive");
  const result = object(execution.result, "execution result");
  const submissionId = text(result.submission_id, "submission_id", 36);
  const archiveSubmissionId = text(archive.submission_id, "archive submission_id", 36);
  const archiveDigest = text(archive.archive_ciphertext_sha256, "archive digest", 64);
  if (!UUID7.test(submissionId) || archiveSubmissionId !== submissionId || !DIGEST.test(archiveDigest)) {
    throw new AuthoritativeReplayContractError("archive identity is invalid");
  }
  const expectation = object(outer.archive_expectation, "archive expectation");
  // The envelope version lives here and is independent of the transport version
  // above: v1 sealed the archive to an age identity, v2 to a 16-byte file key.
  const envelopeVersion = expectation.schema_version;
  if (envelopeVersion !== 1 && envelopeVersion !== 2) {
    throw new AuthoritativeReplayContractError("archive expectation schema_version must be integer 1 or 2");
  }
  exactFields(expectation, [
    "schema_version",
    "submission_id",
    "archive_ciphertext_sha256",
    "plaintext_tar_sha256",
    "plaintext_tar_size",
    ...(envelopeVersion === 2 ? ["key_material_type"] : []),
  ], "archive expectation");
  const plaintextDigest = text(expectation.plaintext_tar_sha256, "plaintext digest", 64);
  const plaintextSize = safeInteger(expectation.plaintext_tar_size, "plaintext size", MAX_PLAINTEXT_BYTES);
  if (
    expectation.submission_id !== submissionId ||
    expectation.archive_ciphertext_sha256 !== archiveDigest ||
    !DIGEST.test(plaintextDigest)
  ) {
    throw new AuthoritativeReplayContractError("archive expectation does not match execution request");
  }
  if (envelopeVersion === 2 && expectation.key_material_type !== "age-file-key-v1") {
    throw new AuthoritativeReplayContractError("archive expectation key material type is invalid");
  }
  const keyMaterialType = outer.key_material_type;
  if (keyMaterialType !== "age-identity-v1" && keyMaterialType !== "age-file-key-v1") {
    throw new AuthoritativeReplayContractError("key material type is invalid");
  }
  // The envelope decides which key form can open the archive, so a request that
  // names the other one is refused rather than handed to the image to fail on.
  if ((envelopeVersion === 2) !== (keyMaterialType === "age-file-key-v1")) {
    throw new AuthoritativeReplayContractError("key material type does not match the archive envelope");
  }
  const keyMaterial = canonicalBase64(
    outer.plaintext_key_material_base64,
    "plaintext_key_material_base64",
    keyMaterialType === "age-file-key-v1" ? 16 : MAX_IDENTITY_BYTES,
  );
  if (keyMaterialType === "age-file-key-v1" && (keyMaterial.length !== 24 || !keyMaterial.endsWith("=="))) {
    throw new AuthoritativeReplayContractError("age file key must contain exactly 16 bytes");
  }
  // The archive itself is not in this request; these two fields only have to
  // name the upload that already assembled it, and the upload record is the
  // thing that must agree with them.
  const archiveBytes = safeInteger(
    outer.archive_ciphertext_bytes,
    "archive_ciphertext_bytes",
    Number.MAX_SAFE_INTEGER,
  );
  const partCount = safeInteger(outer.archive_part_count, "archive_part_count", MAX_ARCHIVE_PART_COUNT);
  return {
    schema_version: 3,
    runner_nonce: runnerNonce,
    request: execution,
    archive_expectation: {
      schema_version: envelopeVersion,
      submission_id: submissionId,
      archive_ciphertext_sha256: archiveDigest,
      plaintext_tar_sha256: plaintextDigest,
      plaintext_tar_size: plaintextSize,
      ...(envelopeVersion === 2 ? { key_material_type: "age-file-key-v1" as const } : {}),
    },
    archive_ciphertext_bytes: archiveBytes,
    archive_part_count: partCount,
    key_material_type: keyMaterialType,
    plaintext_key_material_base64: keyMaterial,
  };
}

export async function readAuthoritativeReplayStatusRequest(
  incoming: Request,
  reviewedProfileDigest: string,
  reviewedMeasurementDigest: string,
  reviewedVmImageDigest: string,
): Promise<AuthoritativeReplayStatusRequest> {
  requireReviewedDigests(
    reviewedProfileDigest,
    reviewedMeasurementDigest,
    reviewedVmImageDigest,
  );
  const maximumBytes = 4096;
  const contentLength = incoming.headers.get("content-length");
  if (contentLength !== null && (!/^\d+$/.test(contentLength) || Number(contentLength) > maximumBytes)) {
    throw new AuthoritativeReplayContractError("status request exceeds the size limit");
  }
  const bytes = new Uint8Array(await incoming.arrayBuffer());
  if (bytes.length === 0 || bytes.length > maximumBytes) {
    throw new AuthoritativeReplayContractError("status request exceeds the size limit");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes));
  } catch {
    throw new AuthoritativeReplayContractError("status request is not one UTF-8 JSON object");
  }
  const status = object(parsed, "status request");
  exactFields(status, [
    "schema_version",
    "runner_nonce",
    "replay_task_id",
    "attempt",
    "execution_profile_digest",
    "measurement_config_digest",
    "vm_image_digest",
  ], "status request");
  const runnerNonce = text(status.runner_nonce, "runner_nonce", 64);
  const replayTaskId = text(status.replay_task_id, "replay_task_id", 68);
  const attempt = safeInteger(status.attempt, "attempt", MAX_REPLAY_ATTEMPTS);
  if (
    status.schema_version !== 1 ||
    !DIGEST.test(runnerNonce) ||
    !REPLAY_ID.test(replayTaskId) ||
    status.execution_profile_digest !== reviewedProfileDigest ||
    status.measurement_config_digest !== reviewedMeasurementDigest ||
    status.vm_image_digest !== reviewedVmImageDigest
  ) {
    throw new AuthoritativeReplayContractError("status request does not match the reviewed execution");
  }
  return {
    schema_version: 1,
    runner_nonce: runnerNonce,
    replay_task_id: replayTaskId,
    attempt,
    execution_profile_digest: reviewedProfileDigest,
    measurement_config_digest: reviewedMeasurementDigest,
    vm_image_digest: reviewedVmImageDigest,
  };
}

function validateCounter(value: unknown, label: string): Record<string, unknown> {
  const counter = object(value, label);
  if (counter.status === "measured") {
    exactFields(counter, ["status", "value"], label);
    nonnegativeSafeInteger(counter.value, `${label}.value`);
  } else if (counter.status === "unavailable") {
    exactFields(counter, ["status", "reason"], label);
    if (![
      "counter_not_reported",
      "counter_not_supported",
      "counter_permission_denied",
    ].includes(counter.reason as string)) {
      throw new AuthoritativeReplayContractError(`${label}.reason is invalid`);
    }
  } else {
    throw new AuthoritativeReplayContractError(`${label}.status is invalid`);
  }
  return counter;
}

export function validateReplayVerdict(
  value: unknown,
  input: Pick<AuthoritativeReplayInput, "request">,
): ReplayVerdict {
  const verdict = object(value, "verdict");
  exactFields(verdict, [
    "schema_version",
    "replay_task_id",
    "attempt",
    "execution_outcome",
    "checker_outcome",
    "failure_reason",
    "statistics",
  ], "verdict");
  const executionOutcome = verdict.execution_outcome;
  const checkerOutcome = verdict.checker_outcome;
  if (
    verdict.schema_version !== 1 ||
    verdict.replay_task_id !== input.request.replay_task_id ||
    verdict.attempt !== input.request.attempt ||
    !["completed", "crashed", "timed_out"].includes(executionOutcome as string) ||
    (executionOutcome === "completed"
      ? !["accepted", "rejected", "declined"].includes(checkerOutcome as string)
      : checkerOutcome !== null) ||
    verdict.failure_reason !== null
  ) {
    throw new AuthoritativeReplayContractError("verdict does not match execution request");
  }
  const statistics = object(verdict.statistics, "verdict statistics");
  exactFields(statistics, [
    "checker_wall_time_ms",
    "checker_retired_instructions",
    "build_wall_time_ms",
    "build_retired_instructions",
    "lines_of_code",
    "file_count",
  ], "verdict statistics");
  for (const field of ["checker_wall_time_ms", "build_wall_time_ms", "lines_of_code", "file_count"] as const) {
    nonnegativeSafeInteger(statistics[field], `verdict statistics ${field}`);
  }
  validateCounter(statistics.checker_retired_instructions, "checker counter");
  validateCounter(statistics.build_retired_instructions, "build counter");
  return verdict as ReplayVerdict;
}
