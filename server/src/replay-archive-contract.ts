const UUID7 = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const BASE64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

const MAX_REQUEST_BYTES = 16 * 1024 * 1024;
const MAX_CIPHERTEXT_BYTES = 11 * 1024 * 1024;
const MAX_PLAINTEXT_BYTES = 10 * 1024 * 1024;
const MAX_IDENTITY_BYTES = 4096;

export type ReplayArchiveAcceptanceRequest = {
  schema_version: 1;
  request_id: string;
  runner_nonce: string;
  submission_id: string;
  archive_ciphertext_sha256: string;
  plaintext_tar_sha256: string;
  plaintext_tar_size: number;
  ciphertext_base64: string;
  plaintext_identity_base64: string;
};

export type ReplayArchiveEvidence = {
  schema_version: 1;
  submission_id: string;
  archive_ciphertext_sha256: string;
  plaintext_tar_sha256: string;
  plaintext_tar_size: number;
  network_probe: "blocked";
  architecture: string;
  kernel_release: string;
  cpu_model: string;
};

export class ReplayArchiveContractError extends Error {}

function object(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ReplayArchiveContractError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactFields(value: Record<string, unknown>, fields: readonly string[], label: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((field, index) => field !== expected[index])) {
    throw new ReplayArchiveContractError(`${label} fields are not canonical`);
  }
}

function text(value: unknown, label: string, maximum: number): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum) {
    throw new ReplayArchiveContractError(`${label} is invalid`);
  }
  return value;
}

function safeInteger(value: unknown, label: string, maximum: number): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1 || (value as number) > maximum) {
    throw new ReplayArchiveContractError(`${label} is invalid`);
  }
  return value as number;
}

function canonicalBase64(value: unknown, label: string, maximumBytes: number): string {
  const encoded = text(value, label, Math.ceil(maximumBytes / 3) * 4);
  if (!BASE64.test(encoded)) throw new ReplayArchiveContractError(`${label} is not canonical base64`);
  const padding = encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0;
  const decodedLength = encoded.length / 4 * 3 - padding;
  if (decodedLength < 1 || decodedLength > maximumBytes) {
    throw new ReplayArchiveContractError(`${label} exceeds its decoded size limit`);
  }
  return encoded;
}

async function sha256Base64(encoded: string): Promise<string> {
  const binary = atob(encoded);
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function readArchiveAcceptanceRequest(
  request: Request,
): Promise<ReplayArchiveAcceptanceRequest> {
  const contentLength = request.headers.get("content-length");
  if (contentLength !== null && (!/^\d+$/.test(contentLength) || Number(contentLength) > MAX_REQUEST_BYTES)) {
    throw new ReplayArchiveContractError("request exceeds the size limit");
  }
  const bytes = new Uint8Array(await request.arrayBuffer());
  if (bytes.length === 0 || bytes.length > MAX_REQUEST_BYTES) {
    throw new ReplayArchiveContractError("request exceeds the size limit");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes));
  } catch {
    throw new ReplayArchiveContractError("request is not one UTF-8 JSON object");
  }
  const value = object(parsed, "request");
  exactFields(value, [
    "schema_version",
    "request_id",
    "runner_nonce",
    "submission_id",
    "archive_ciphertext_sha256",
    "plaintext_tar_sha256",
    "plaintext_tar_size",
    "ciphertext_base64",
    "plaintext_identity_base64",
  ], "request");
  if (value.schema_version !== 1) throw new ReplayArchiveContractError("schema_version must be integer 1");
  const requestId = text(value.request_id, "request_id", 36);
  const runnerNonce = text(value.runner_nonce, "runner_nonce", 64);
  const submissionId = text(value.submission_id, "submission_id", 36);
  const archiveDigest = text(value.archive_ciphertext_sha256, "archive_ciphertext_sha256", 64);
  const plaintextDigest = text(value.plaintext_tar_sha256, "plaintext_tar_sha256", 64);
  if (!UUID7.test(requestId) || !UUID7.test(submissionId)) {
    throw new ReplayArchiveContractError("UUIDv7 identity is not canonical");
  }
  if (!DIGEST.test(runnerNonce) || !DIGEST.test(archiveDigest) || !DIGEST.test(plaintextDigest)) {
    throw new ReplayArchiveContractError("digest is not canonical");
  }
  const plaintextSize = safeInteger(value.plaintext_tar_size, "plaintext_tar_size", MAX_PLAINTEXT_BYTES);
  const ciphertext = canonicalBase64(value.ciphertext_base64, "ciphertext_base64", MAX_CIPHERTEXT_BYTES);
  const identity = canonicalBase64(
    value.plaintext_identity_base64,
    "plaintext_identity_base64",
    MAX_IDENTITY_BYTES,
  );
  if (await sha256Base64(ciphertext) !== archiveDigest) {
    throw new ReplayArchiveContractError("ciphertext digest does not match request");
  }
  return {
    schema_version: 1,
    request_id: requestId,
    runner_nonce: runnerNonce,
    submission_id: submissionId,
    archive_ciphertext_sha256: archiveDigest,
    plaintext_tar_sha256: plaintextDigest,
    plaintext_tar_size: plaintextSize,
    ciphertext_base64: ciphertext,
    plaintext_identity_base64: identity,
  };
}

export function validateArchiveEvidence(
  value: unknown,
  request: ReplayArchiveAcceptanceRequest,
): ReplayArchiveEvidence {
  const evidence = object(value, "sandbox evidence");
  exactFields(evidence, [
    "schema_version",
    "submission_id",
    "archive_ciphertext_sha256",
    "plaintext_tar_sha256",
    "plaintext_tar_size",
    "network_probe",
    "architecture",
    "kernel_release",
    "cpu_model",
  ], "sandbox evidence");
  const expected = {
    schema_version: 1,
    submission_id: request.submission_id,
    archive_ciphertext_sha256: request.archive_ciphertext_sha256,
    plaintext_tar_sha256: request.plaintext_tar_sha256,
    plaintext_tar_size: request.plaintext_tar_size,
    network_probe: "blocked",
  } as const;
  for (const [field, expectedValue] of Object.entries(expected)) {
    if (evidence[field] !== expectedValue) {
      throw new ReplayArchiveContractError(`sandbox evidence ${field} does not match request`);
    }
  }
  return {
    ...expected,
    architecture: text(evidence.architecture, "architecture", 128),
    kernel_release: text(evidence.kernel_release, "kernel_release", 256),
    cpu_model: text(evidence.cpu_model, "cpu_model", 256),
  };
}
