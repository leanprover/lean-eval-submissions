const UUID7 = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const BASE64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

const MAX_REQUEST_BYTES = 512 * 1024;
const MAX_CIPHERTEXT_BYTES = 128 * 1024;
const MAX_IDENTITY_BYTES = 4096;

export type ReplayAcceptanceRequest = {
  schema_version: 1;
  request_id: string;
  runner_nonce: string;
  archive_ciphertext_sha256: string;
  ciphertext_base64: string;
  plaintext_identity_base64: string;
  marker_sha256: string;
};

export type SandboxEvidence = {
  schema_version: 1;
  archive_ciphertext_sha256: string;
  marker_sha256: string;
  network_probe: "blocked";
  architecture: string;
  kernel_release: string;
  cpu_model: string;
};

export class ReplayContractError extends Error {}

function object(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ReplayContractError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactFields(value: Record<string, unknown>, fields: readonly string[], label: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((field, index) => field !== expected[index])) {
    throw new ReplayContractError(`${label} fields are not canonical`);
  }
}

function string(value: unknown, label: string, maximum: number): string {
  if (typeof value !== "string" || value.length === 0 || value.length > maximum) {
    throw new ReplayContractError(`${label} is invalid`);
  }
  return value;
}

function canonicalBase64(value: unknown, label: string, maximumBytes: number): string {
  const encoded = string(value, label, Math.ceil(maximumBytes / 3) * 4);
  if (!BASE64.test(encoded)) throw new ReplayContractError(`${label} is not canonical base64`);
  const padding = encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0;
  const decodedLength = encoded.length === 0 ? 0 : encoded.length / 4 * 3 - padding;
  if (decodedLength < 1 || decodedLength > maximumBytes) {
    throw new ReplayContractError(`${label} exceeds its decoded size limit`);
  }
  return encoded;
}

async function sha256Base64(encoded: string): Promise<string> {
  const binary = atob(encoded);
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function readAcceptanceRequest(request: Request): Promise<ReplayAcceptanceRequest> {
  const contentLength = request.headers.get("content-length");
  if (contentLength !== null && (!/^\d+$/.test(contentLength) || Number(contentLength) > MAX_REQUEST_BYTES)) {
    throw new ReplayContractError("request exceeds the size limit");
  }
  const bytes = new Uint8Array(await request.arrayBuffer());
  if (bytes.length === 0 || bytes.length > MAX_REQUEST_BYTES) {
    throw new ReplayContractError("request exceeds the size limit");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes));
  } catch {
    throw new ReplayContractError("request is not one UTF-8 JSON object");
  }
  const value = object(parsed, "request");
  exactFields(value, [
    "schema_version",
    "request_id",
    "runner_nonce",
    "archive_ciphertext_sha256",
    "ciphertext_base64",
    "plaintext_identity_base64",
    "marker_sha256",
  ], "request");
  if (value.schema_version !== 1) throw new ReplayContractError("schema_version must be integer 1");
  const requestId = string(value.request_id, "request_id", 36);
  const runnerNonce = string(value.runner_nonce, "runner_nonce", 64);
  const archiveDigest = string(value.archive_ciphertext_sha256, "archive_ciphertext_sha256", 64);
  const markerDigest = string(value.marker_sha256, "marker_sha256", 64);
  if (!UUID7.test(requestId)) throw new ReplayContractError("request_id is not UUIDv7");
  if (!DIGEST.test(runnerNonce)) throw new ReplayContractError("runner_nonce is not canonical");
  if (!DIGEST.test(archiveDigest) || !DIGEST.test(markerDigest)) {
    throw new ReplayContractError("digest is not canonical");
  }
  const ciphertext = canonicalBase64(value.ciphertext_base64, "ciphertext_base64", MAX_CIPHERTEXT_BYTES);
  const identity = canonicalBase64(
    value.plaintext_identity_base64,
    "plaintext_identity_base64",
    MAX_IDENTITY_BYTES,
  );
  if (await sha256Base64(ciphertext) !== archiveDigest) {
    throw new ReplayContractError("ciphertext digest does not match request");
  }
  return {
    schema_version: 1,
    request_id: requestId,
    runner_nonce: runnerNonce,
    archive_ciphertext_sha256: archiveDigest,
    ciphertext_base64: ciphertext,
    plaintext_identity_base64: identity,
    marker_sha256: markerDigest,
  };
}

export function validateSandboxEvidence(value: unknown, request: ReplayAcceptanceRequest): SandboxEvidence {
  const evidence = object(value, "sandbox evidence");
  exactFields(evidence, [
    "schema_version",
    "archive_ciphertext_sha256",
    "marker_sha256",
    "network_probe",
    "architecture",
    "kernel_release",
    "cpu_model",
  ], "sandbox evidence");
  if (evidence.schema_version !== 1) throw new ReplayContractError("evidence schema_version is invalid");
  if (evidence.archive_ciphertext_sha256 !== request.archive_ciphertext_sha256) {
    throw new ReplayContractError("evidence archive digest does not match request");
  }
  if (evidence.marker_sha256 !== request.marker_sha256) {
    throw new ReplayContractError("evidence marker digest does not match request");
  }
  if (evidence.network_probe !== "blocked") {
    throw new ReplayContractError("sandbox did not prove disabled network access");
  }
  return {
    schema_version: 1,
    archive_ciphertext_sha256: request.archive_ciphertext_sha256,
    marker_sha256: request.marker_sha256,
    network_probe: "blocked",
    architecture: string(evidence.architecture, "architecture", 128),
    kernel_release: string(evidence.kernel_release, "kernel_release", 256),
    cpu_model: string(evidence.cpu_model, "cpu_model", 256),
  };
}
